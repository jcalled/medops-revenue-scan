"""
Acompanhamento da recuperação — a base da cobrança.

O que estes testes travam:
- entra na base só AIH corrigível, rejeitada antes do início e não recebida antes dele;
- AIH rejeitada durante o acompanhamento entra como NOVA;
- recuperada é a marcada que aparece aprovada depois da rejeição, com o valor do RD;
- a fatura do mês é fixo por hospital + percentual do valor aprovado, com o arquivo RD;
- conferir de novo não duplica e pega aprovação de mês novo;
- só a administração abre e confere; o tenant vê só os dele.
"""
from datetime import date

import httpx

from app.domain.recuperacao import conferir_ativos
from app.models import (
    DataLoad, Establishment, ManagementOrganization, OrganizationEstablishment, SihApprovedAih, SihErrorCode,
    SihRejection, SihRejectionReason,
)
from tests.conftest import contrato, token

A, B = "9672427", "6779522"
ADMIN = {"Authorization": f"Bearer {token(role='platform_admin', tenant_id=None)}"}


def _rejeitar(db, competencia, n_aih, valor, codigo, cnes=A, dt_saida=None):
    db.add(SihRejection(uf="CE", competencia=competencia, cnes=cnes, n_aih=n_aih, valor=valor, dt_saida=dt_saida,
                        proc_realizado="0303010010", competencia_aih=competencia))
    db.add(SihRejectionReason(uf="CE", competencia=competencia, cnes=cnes, n_aih=n_aih, codigo_erro=codigo))


def _aprovar(db, competencia, n_aih, valor, cnes=A):
    db.add(SihApprovedAih(uf="CE", competencia=competencia, cnes=cnes, n_aih=n_aih, valor=valor))


def _dados(fabrica_sessao):
    with fabrica_sessao() as db:
        db.add(Establishment(cnes=A, nome_fantasia="HOSPITAL A", uf="CE", natureza_juridica="1023"))
        db.add(SihErrorCode(codigo="060082", descricao="QUANTIDADE DE DIÁRIAS SUPERIOR A CAPACIDADE INSTALADA"))
        _rejeitar(db, "202605", "1", 1000, "060082")
        _aprovar(db, "202607", "1", 1100)                    # base, recuperada em julho
        _rejeitar(db, "202605", "2", 2000, "060082")          # base, em aberto
        _rejeitar(db, "202605", "3", 9000, "010003")          # bloqueio do gestor: fora
        _rejeitar(db, "202605", "4", 700, "060082")
        _aprovar(db, "202605", "4", 700)                      # já recebida antes do início: fora
        _rejeitar(db, "202606", "5", 500, "060082")
        _aprovar(db, "202607", "5", 480)                      # nova, recuperada em julho
        _rejeitar(db, "202607", "6", 300, "040008", dt_saida=date(2026, 3, 10))  # nova, prazo até jul/26
        _rejeitar(db, "202605", "7", 4000, "060082", cnes=B)  # outro hospital
        db.add(DataLoad(fonte="SIH_RD", uf="CE", competencia="202607", origem="DOWNLOAD", arquivo="RDCE2607.dbc",
                        checksum="rd07", status="OK"))
        db.commit()


def _http_admin(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato(platform_admin=True, tenant_id=None,
                                                                          role="platform_admin")))
    return http


def _abrir(http, **extra):
    corpo = {"nome": "ISGH piloto", "cnes": [A], "inicio": "202606", "tenant_id": 1, **extra}
    resposta = http.post("/api/revenue-scan/recovery", headers=ADMIN, json=corpo)
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def test_marca_base_e_nova_e_confere_recuperadas(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http = _http_admin(app_com_nucleo)
    aberto = _abrir(http)
    assert aberto["hospitais"] == [{"cnes": A, "nome": "HOSPITAL A"}] and aberto["ultimo_mes_carregado"] == "202607"

    detalhe = http.get(f"/api/revenue-scan/recovery/{aberto['id']}", headers=ADMIN).json()
    itens = {i["n_aih"]: i for i in detalhe["itens"]}
    assert set(itens) == {"1", "2", "5", "6"}
    assert (itens["1"]["origem"], itens["1"]["situacao"], itens["1"]["competencia_aprovacao"], itens["1"]["valor_aprovado"]) \
        == ("BASE", "RECUPERADA", "202607", 1100.0)
    assert (itens["2"]["origem"], itens["2"]["situacao"]) == ("BASE", "EM_ABERTO")
    assert (itens["5"]["origem"], itens["5"]["situacao"], itens["5"]["valor_aprovado"]) == ("NOVA", "RECUPERADA", 480.0)
    assert (itens["6"]["origem"], itens["6"]["prazo_estimado"], itens["6"]["prazo"]) == ("NOVA", "202609", None)
    assert itens["2"]["motivos"] == [{"codigo": "060082", "descricao": "QUANTIDADE DE DIÁRIAS SUPERIOR A CAPACIDADE INSTALADA"}]
    assert [i["situacao"] for i in detalhe["itens"]][:2] == ["EM_ABERTO", "EM_ABERTO"]

    r = detalhe["resumo"]
    assert r["marcadas"] == {"aih": 4, "valor": 3800.0}
    assert r["base"] == {"aih": 2, "valor": 3000.0} and r["novas"] == {"aih": 2, "valor": 800.0}
    assert r["recuperadas"] == {"aih": 2, "valor": 1580.0, "valor_rejeitado": 1500.0}
    assert r["em_aberto"] == {"aih": 2, "valor": 2300.0} and r["taxa_recuperada"] == 0.5
    assert r["por_mes"] == [{"competencia": "202607", "aih": 2, "valor": 1580.0}]
    assert r["prazo_vencido"] == {"aih": 0, "valor": 0.0}


def test_fatura_do_mes(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http = _http_admin(app_com_nucleo)
    aberto = _abrir(http)
    fatura = http.get(f"/api/revenue-scan/recovery/{aberto['id']}/invoice?competencia=202607", headers=ADMIN).json()
    assert fatura["totais"] == {"aih": 2, "valor_recuperado": 1580.0, "linha_de_base": 0.0, "excedente": 1580.0,
                                "fixo": 6900.0, "variavel": 237.0, "total": 7137.0}
    assert fatura["hospitais"] == [{"cnes": A, "nome": "HOSPITAL A", "aih": 2, "valor_recuperado": 1580.0,
                                    "linha_de_base": 0.0, "excedente": 1580.0, "fixo": 6900.0, "variavel": 237.0,
                                    "total": 7137.0}]
    # Sem meses carregados antes do início com histórico, não há o que descontar — e a fatura diz.
    assert fatura["linha_de_base"] == {"origem": "CALCULADA", "meses": [], "mensal": 0.0}
    primeira = fatura["linhas"][0]
    assert primeira["n_aih"] == "1" and primeira["valor_cobrado"] == 1100.0 and not primeira["valor_estimado"]
    assert primeira["arquivo_rd"] == {"arquivo": "RDCE2607.dbc", "sha256": "rd07"}

    vazia = http.get(f"/api/revenue-scan/recovery/{aberto['id']}/invoice?competencia=202606", headers=ADMIN).json()
    assert vazia["totais"] == {"aih": 0, "valor_recuperado": 0.0, "linha_de_base": 0.0, "excedente": 0.0,
                               "fixo": 6900.0, "variavel": 0.0, "total": 6900.0}
    assert http.get(f"/api/revenue-scan/recovery/{aberto['id']}/invoice?competencia=2026-07",
                    headers=ADMIN).status_code == 422

    mudado = http.patch(f"/api/revenue-scan/recovery/{aberto['id']}", headers=ADMIN, json={"percentual": 20}).json()
    assert mudado["percentual"] == 20.0
    fatura = http.get(f"/api/revenue-scan/recovery/{aberto['id']}/invoice?competencia=202607", headers=ADMIN).json()
    assert fatura["totais"]["variavel"] == 316.0


def test_desconta_o_que_o_hospital_ja_recuperava_sozinho(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        _aprovar(db, "202601", "x1", 50)                      # jan/26 carregado, sem rejeição antes
        _rejeitar(db, "202602", "b1", 1000, "060082")
        _aprovar(db, "202604", "b1", 900)                     # voltou sozinha em abril
        _rejeitar(db, "202603", "b2", 400, "060082")
        _aprovar(db, "202605", "b2", 300)                     # voltou sozinha em maio
        _rejeitar(db, "202603", "b3", 800, "010003")
        _aprovar(db, "202604", "b3", 800)                     # bloqueio do gestor: não é recuperação
        db.commit()
    http = _http_admin(app_com_nucleo)
    aberto = _abrir(http)
    # Meses com dois carregados antes e anteriores ao início: mar, abr e mai. (900 + 300) / 3.
    assert aberto["linha_de_base"] == {"por_hospital": {A: 400.0}, "mensal": 400.0,
                                       "meses": ["202603", "202604", "202605"], "origem": "CALCULADA"}
    caminho = f"/api/revenue-scan/recovery/{aberto['id']}"

    fatura = http.get(f"{caminho}/invoice?competencia=202607", headers=ADMIN).json()
    assert fatura["hospitais"][0] | {} == {**fatura["hospitais"][0], "valor_recuperado": 1580.0, "linha_de_base": 400.0,
                                           "excedente": 1180.0, "variavel": 177.0, "total": 7077.0}
    assert fatura["totais"]["excedente"] == 1180.0 and fatura["linha_de_base"]["mensal"] == 400.0

    negociada = http.patch(caminho, headers=ADMIN, json={"linha_de_base": {A: 1000}}).json()
    assert negociada["linha_de_base"]["origem"] == "NEGOCIADA" and negociada["linha_de_base"]["mensal"] == 1000.0
    assert http.get(f"{caminho}/invoice?competencia=202607", headers=ADMIN).json()["totais"]["variavel"] == 87.0
    assert http.patch(caminho, headers=ADMIN, json={"linha_de_base": {"0000001": 5}}).status_code == 422

    # Hospital abaixo da base não paga percentual, e não fica negativo.
    http.patch(caminho, headers=ADMIN, json={"linha_de_base": {A: 5000}})
    assert http.get(f"{caminho}/invoice?competencia=202607", headers=ADMIN).json()["totais"] == {
        "aih": 2, "valor_recuperado": 1580.0, "linha_de_base": 5000.0, "excedente": 0.0, "fixo": 6900.0,
        "variavel": 0.0, "total": 6900.0}

    recalculada = http.post(f"{caminho}/baseline", headers=ADMIN).json()
    assert recalculada["linha_de_base"]["origem"] == "CALCULADA" and recalculada["linha_de_base"]["mensal"] == 400.0


def test_conferir_de_novo_nao_duplica(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http = _http_admin(app_com_nucleo)
    aberto = _abrir(http)
    with fabrica_sessao() as db:
        _aprovar(db, "202608", "2", 2100)
        _rejeitar(db, "202608", "8", 600, "060082")
        db.commit()
    caminho = f"/api/revenue-scan/recovery/{aberto['id']}"
    assert http.post(f"{caminho}/check", headers=ADMIN).json() == {"novas": 1, "recuperadas": 1}
    assert http.post(f"{caminho}/check", headers=ADMIN).json() == {"novas": 0, "recuperadas": 0}
    resumo = http.get(caminho, headers=ADMIN).json()["resumo"]
    assert resumo["recuperadas"]["aih"] == 3 and resumo["marcadas"]["aih"] == 5

    # Depois de cada carga o worker confere os ativos; encerrado não.
    http.patch(caminho, headers=ADMIN, json={"status": "ENCERRADO"})
    assert http.post(f"{caminho}/check", headers=ADMIN).status_code == 409
    with fabrica_sessao() as db:
        assert conferir_ativos(db) == 0


def test_permissoes_e_tenant(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    aberto = _abrir(_http_admin(app_com_nucleo))

    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    tenant1 = {"Authorization": f"Bearer {token()}"}
    assert http.post("/api/revenue-scan/recovery", headers=tenant1,
                     json={"nome": "x", "cnes": [A], "inicio": "202606"}).status_code == 403
    assert [a["id"] for a in http.get("/api/revenue-scan/recovery", headers=tenant1).json()["acompanhamentos"]] == [aberto["id"]]
    assert http.get(f"/api/revenue-scan/recovery/{aberto['id']}", headers=tenant1).status_code == 200
    assert http.post(f"/api/revenue-scan/recovery/{aberto['id']}/check", headers=tenant1).status_code == 403

    # Escopo do contrato sem o hospital: some.
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [B]})))
    assert http.get(f"/api/revenue-scan/recovery/{aberto['id']}", headers=tenant1).status_code == 404

    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato(tenant_id=2)))
    tenant2 = {"Authorization": f"Bearer {token(tenant_id=2)}"}
    assert http.get("/api/revenue-scan/recovery", headers=tenant2).json() == {"acompanhamentos": []}
    assert http.get(f"/api/revenue-scan/recovery/{aberto['id']}", headers=tenant2).status_code == 404


def test_abre_pela_organizacao(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        org = ManagementOrganization(sigla="ISGH", nome="Instituto de Saúde e Gestão Hospitalar", uf="CE")
        org.unidades.append(OrganizationEstablishment(cnes=A, sigla="HRVJ", situacao="CONFIRMADO"))
        db.add(org)
        db.commit()
        org_id = org.id
    http = _http_admin(app_com_nucleo)
    aberto = _abrir(http, cnes=None, organization_id=org_id)
    assert aberto["cnes"] == [A] and aberto["organizacao"]["sigla"] == "ISGH"
    assert http.post("/api/revenue-scan/recovery", headers=ADMIN,
                     json={"nome": "sem hospitais", "inicio": "202606"}).status_code == 422
