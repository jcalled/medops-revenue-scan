"""
Alerta mensal por organização.

O que estes testes travam:
- o conteúdo: mês mais recente, rejeitado no mês, o que o FaturaSUS teria pegado, o que vence e o recuperável;
- um alerta por organização e mês novo; "enviar agora" refaz e reenvia;
- sem e-mail configurado, fica só na tela; sem destinatário, registra; falha de envio não derruba os outros;
- o agendador enfileira só os meses publicados que faltam nas UFs com alerta ligado;
- configurar e enviar é da administração da plataforma; e-mail inválido é recusado.
"""
from dataclasses import replace

import httpx

from app.config import get_settings
from app.domain.alertas import gerar_alertas, montar_alerta, renderizar
from app.jobs.agenda import verificar
from app.models import AlertIssue, AlertSubscription, ManagementOrganization, OrganizationEstablishment
from tests.conftest import contrato, token
from tests.test_dados import ADMIN, _admin
from tests.test_kit import _dados
from tests.test_scan_api import HRC, HRVJ

COM_EMAIL = replace(get_settings(), smtp_host="smtp.exemplo", smtp_from="alertas@medops.com.br")


def _org(fabrica_sessao, emails=("financeiro@oss.org.br",), incluir_honorarios=False):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        org = ManagementOrganization(sigla="OSSX", nome="OSS Exemplo", uf="CE")
        org.unidades = [OrganizationEstablishment(cnes=HRVJ, situacao="CONFIRMADO"), OrganizationEstablishment(cnes=HRC, situacao="CONFIRMADO")]
        db.add(org)
        db.flush()
        db.add(AlertSubscription(organization_id=org.id, emails=list(emails), ativo=True, incluir_honorarios=incluir_honorarios, percentual=15))
        db.commit()
        return org.id


def test_conteudo_do_alerta(fabrica_sessao):
    org_id = _org(fabrica_sessao, incluir_honorarios=True)
    with fabrica_sessao() as db:
        org = db.get(ManagementOrganization, org_id)
        c = montar_alerta(db, org, referencia="202610", assinatura=db.query(AlertSubscription).one())
    assert c["competencia"] == "202607"                                  # o mês mais recente carregado
    assert c["rejeitado_mes"] == {"aih": 1, "valor": 700.0}              # só a B1, do HRC, é de julho
    assert c["vence_neste_mes"] == {"aih": 1, "valor": 2000.0}
    assert c["recuperavel"]["valor"] == 9000.0 and c["honorarios"]["sobre_recuperavel"] == 1350.0
    assunto, html, texto = renderizar(c, "https://app.exemplo")
    assert assunto.startswith("OSSX: R$ 2.000 vencem neste mês") and "https://app.exemplo/revenue-scan/prevencao" in html
    assert "FaturaSUS teria pegado" in texto


def test_um_por_mes_e_reenvio(fabrica_sessao):
    org_id = _org(fabrica_sessao)
    enviados = []
    def enviar(settings, destinos, assunto, html, texto):
        enviados.append((tuple(destinos), assunto))
    with fabrica_sessao() as db:
        [e] = gerar_alertas(db, ufs=["CE"], settings=COM_EMAIL, enviar=enviar)
        assert (e.status, e.destinatarios, e.competencia) == ("ENVIADO", ["financeiro@oss.org.br"], "202607")
        assert gerar_alertas(db, ufs=["CE"], settings=COM_EMAIL, enviar=enviar) == []      # mesmo mês: não repete
        assert gerar_alertas(db, ufs=["SP"], settings=COM_EMAIL, enviar=enviar) == []      # outra UF: nada
        [de_novo] = gerar_alertas(db, organizacao=org_id, forcar=True, settings=COM_EMAIL, enviar=enviar)
        assert de_novo.id == e.id and len(enviados) == 2
        assert db.query(AlertIssue).count() == 1


def test_sem_email_sem_destinatario_e_falha(fabrica_sessao):
    org_id = _org(fabrica_sessao)
    with fabrica_sessao() as db:
        sem_smtp = replace(get_settings(), smtp_host="", smtp_from="")
        [e] = gerar_alertas(db, settings=sem_smtp)
        assert e.status == "SO_NA_TELA"
        def falha(*_):
            raise OSError("servidor recusou")
        [e] = gerar_alertas(db, organizacao=org_id, forcar=True, settings=COM_EMAIL, enviar=falha)
        assert e.status == "FALHOU" and "servidor recusou" in e.erro
        db.query(AlertSubscription).one().emails = []
        db.commit()
        [e] = gerar_alertas(db, organizacao=org_id, forcar=True, settings=COM_EMAIL)
        assert e.status == "SEM_DESTINATARIO"


def test_agendador_baixa_so_o_que_falta(fabrica_sessao):
    _org(fabrica_sessao)

    class Falso:
        def competencias_disponiveis(self, uf):
            return ["202605", "202606", "202607", "202608"]

    pedidos = []
    with fabrica_sessao() as db:
        r = verificar(db, adapters={t: Falso() for t in ("RD", "RJ", "ER")}, ocupadas=set(),
                      enfileirar=lambda uf, comps, n: pedidos.append((uf, comps)) or f"job-{uf}")
    [ce] = r
    assert ce["uf"] == "CE" and ce["situacao"] == "ENFILEIRADA" and "202608" in ce["faltando"]
    assert pedidos == [("CE", ce["faltando"])]


def test_rotas_da_tela(app_com_nucleo, fabrica_sessao, monkeypatch):
    org_id = _org(fabrica_sessao, emails=())
    http = _admin(app_com_nucleo)
    cliente = {"Authorization": f"Bearer {token()}"}
    assert http.get("/api/revenue-scan/alerts", headers=cliente).status_code == 403
    assert http.put(f"/api/revenue-scan/alerts/organizations/{org_id}", json={"emails": ["x@"]}, headers=ADMIN).status_code == 422
    r = http.put(f"/api/revenue-scan/alerts/organizations/{org_id}", headers=ADMIN,
                 json={"emails": ["Diretoria@OSS.org.br", "diretoria@oss.org.br"], "incluir_honorarios": True})
    assert r.json()["emails"] == ["diretoria@oss.org.br"]
    previa = http.get(f"/api/revenue-scan/alerts/organizations/{org_id}/preview", headers=ADMIN).json()
    assert previa["assunto"].startswith("OSSX:") and "<table" in previa["html"]
    monkeypatch.setattr("app.domain.alertas.get_settings", lambda: replace(get_settings(), smtp_host="", smtp_from=""))
    envio = http.post(f"/api/revenue-scan/alerts/organizations/{org_id}/send", headers=ADMIN).json()
    assert envio["status"] == "SO_NA_TELA" and envio["sigla"] == "OSSX"
    painel = http.get("/api/revenue-scan/alerts", headers=ADMIN).json()
    assert [e["id"] for e in painel["envios"]] == [envio["id"]] and painel["assinaturas"][0]["incluir_honorarios"]
