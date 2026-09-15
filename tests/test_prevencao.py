"""
Prevenção: o que o FaturaSUS teria pegado antes do envio.

O que estes testes travam:
- a carga guarda o acumulado do lote na ordem da remessa, com as diárias das rejeitadas pelas datas;
- recarregar a competência apaga a prevenção antiga dela;
- a AIH entra no grupo mais favorável: um motivo pego basta; não medível não vira "não pegou";
- o resumo mede a taxa só sobre o conferível e separa o que não foi avaliado;
- a prova AIH por AIH e o painel mostram o resultado;
- sem a chave de serviço a prevenção não roda, e falha do motor fica registrada;
- o cliente manda a chave e trata recusa como motor indisponível.
"""
from dataclasses import replace
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select, update

from app.adapters.faturasus import FaturaSusNucleo, MotorIndisponivel
from app.config import get_settings
from app.domain.prevencao import avaliar_competencia, grupo_da_aih, resumo_prevencao
from app.jobs.carga_sih import acumulado_do_lote, carregar_competencia
from app.jobs.prevencao import job_prevencao_uf, rodar
from app.jobs.recalcular import recalcular
from app.models import DataLoad, SihPrevention, SihRejection, SihRejectionReason
from tests.conftest import contrato, token
from tests.test_carga_sih import MAIO, adapters, aih
from tests.test_scan_api import HRC, HRVJ, popular

GESTOR, ARQUIVO, SEM_CAMPOS = "2326999990001", "2326999990002", "2326999990003"
PEGOU = [{"codigo": "060082", "grupo": "CONFERIVEL", "regras": ["SUS_DIARIAS_CAPACIDADE_ALERTA"], "pegaria": True}]
NAO_PEGOU = [{"codigo": "060082", "grupo": "CONFERIVEL", "regras": [], "pegaria": False}]


def test_acumulado_do_lote_segue_a_remessa():
    def linha(n, cnes, diarias, sequencia, **extra):
        return {**aih(n, cnes, 100.0, diarias=diarias), "competencia_aih": "202605", "remessa": "1",
                "sequencia": sequencia, **extra}

    maio = {"dt_internacao": date(2026, 5, 1), "dt_saida": date(2026, 5, 8)}
    aprovadas = {"A1": linha("A1", HRVJ, 10, 1), "A2": linha("A2", HRVJ, 5, 3), "A3": linha("A3", HRC, 50, 1)}
    rejeitadas = {"R1": linha("R1", HRVJ, 0, 2, **maio), "R2": linha("R2", HRVJ, 0, 4, **maio)}
    antes, antes_uti = acumulado_do_lote(aprovadas, rejeitadas)
    # R1 vem depois de A1; R2 depois de A1, R1 (7 dias pelas datas) e A2. Outro hospital não conta.
    assert antes == {"R1": 10, "R2": 22} and antes_uti == {"R1": 0, "R2": 0}


def test_grupo_da_aih():
    assert grupo_da_aih(PEGOU + [{"grupo": "BLOQUEIO_DO_GESTOR"}]) == "PEGARIA"
    assert grupo_da_aih(NAO_PEGOU + [{"grupo": "PRECISA_ARQUIVO_DO_HOSPITAL"}]) == "CONFERIVEL_NAO_PEGOU"
    assert grupo_da_aih([{"grupo": "PRECISA_ARQUIVO_DO_HOSPITAL"}, {"grupo": "BLOQUEIO_DO_GESTOR"}]) == "PRECISA_ARQUIVO"
    assert grupo_da_aih([{"grupo": "BLOQUEIO_DO_GESTOR"}]) == "GESTOR"
    assert grupo_da_aih([]) == "SEM_MOTIVO"


class MotorFalso:
    def __init__(self, quebra=False):
        self.pedidos, self.quebra = [], quebra

    def avaliar(self, aihs):
        if self.quebra:
            raise MotorIndisponivel("fora do ar")
        self.pedidos.extend(aihs)
        respostas = {f"23260000000{i:02d}": (PEGOU if i < 20 else NAO_PEGOU) for i in range(30)}
        respostas[GESTOR] = [{"codigo": "010003", "grupo": "BLOQUEIO_DO_GESTOR", "regras": [], "pegaria": False}]
        respostas[ARQUIVO] = [{"codigo": "060109", "grupo": "PRECISA_ARQUIVO_DO_HOSPITAL", "regras": [], "pegaria": False}]
        return {"referencias": {"SIGTAP": "202609"}, "resultados": [
            {"n_aih": a["n_aih"], "motivos": respostas[a["n_aih"]], "falhas": [], "avisos": ["SUS_DIARIAS_CAPACIDADE_ALERTA"],
             "mensagens": []} for a in aihs]}


def _dados(fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)  # 30 AIH do HRVJ rejeitadas por 060082 em maio, R$ 15 mil cada
        db.add_all([
            SihRejection(uf="CE", competencia="202606", cnes=HRVJ, n_aih=GESTOR, valor=1000.0),
            SihRejectionReason(uf="CE", competencia="202606", cnes=HRVJ, n_aih=GESTOR, codigo_erro="010003"),
            SihRejection(uf="CE", competencia="202606", cnes=HRVJ, n_aih=ARQUIVO, valor=2000.0),
            SihRejectionReason(uf="CE", competencia="202606", cnes=HRVJ, n_aih=ARQUIVO, codigo_erro="060109"),
            SihRejection(uf="CE", competencia="202606", cnes=HRVJ, n_aih=SEM_CAMPOS, valor=500.0),
        ])
        db.commit()
        db.execute(update(SihRejection).where(SihRejection.n_aih != SEM_CAMPOS)
                   .values(campos={"CNES": HRVJ, "MES_CMPT": "05", "ANO_CMPT": "2026"}, diarias_antes=2900))
        db.commit()
        recalcular(db, ufs=["CE"])


def test_avalia_guarda_e_resume(fabrica_sessao):
    _dados(fabrica_sessao)
    motor = MotorFalso()
    with fabrica_sessao() as db:
        assert rodar(db, "CE", None, motor) == [
            {"competencia": "202605", "avaliadas": 30, "pegaria": 20, "sem_campos": 0},
            {"competencia": "202606", "avaliadas": 2, "pegaria": 0, "sem_campos": 1},
        ]
        primeiro = next(p for p in motor.pedidos if p["n_aih"] == "2326000000001")
        assert primeiro["motivos"] == ["060082"] and primeiro["diarias_antes"] == 2900 and primeiro["campos"]["CNES"] == HRVJ
        assert [c.linhas for c in db.execute(select(DataLoad).where(DataLoad.fonte == "FATURASUS")).scalars()] == [30, 2]

        resumo = resumo_prevencao(db, [HRVJ], ["202605", "202606", "202607"])
    assert resumo["rejeitadas"] == {"aih": 33, "valor": 453500.0}
    assert resumo["pegaria"] == {"aih": 20, "valor": 300000.0}
    assert resumo["conferivel"] == {"aih": 30, "valor": 450000.0} and resumo["taxa_conferivel"] == 0.6667
    assert resumo["grupos"]["GESTOR"]["aih"] == 1 and resumo["grupos"]["PRECISA_ARQUIVO"]["aih"] == 1
    assert resumo["nao_avaliadas"] == {"aih": 1, "valor": 500.0}
    assert resumo["regras_que_pegaram"] == [{"regra": "SUS_DIARIAS_CAPACIDADE_ALERTA", "aih": 20}]

    # Rodar de novo substitui, não soma.
    with fabrica_sessao() as db:
        avaliar_competencia(db, "CE", "202605", MotorFalso())
        assert db.scalar(select(func.count()).select_from(SihPrevention)) == 32


def test_prova_e_painel_mostram_a_prevencao(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        rodar(db, "CE", None, MotorFalso())
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    cabecalho = {"Authorization": f"Bearer {token()}"}

    prova = http.get(f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas", headers=cabecalho).json()
    assert prova["prevencao"]["pegaria"]["aih"] == 20
    linhas = {l["n_aih"]: l for l in prova["linhas"]}
    assert linhas["2326000000001"]["prevencao"]["grupo"] == "PEGARIA"
    assert linhas["2326000000001"]["prevencao"]["regras"] == ["SUS_DIARIAS_CAPACIDADE_ALERTA"]
    assert linhas["2326000000025"]["prevencao"]["grupo"] == "CONFERIVEL_NAO_PEGOU"
    assert linhas[SEM_CAMPOS]["prevencao"] is None

    painel = http.get("/api/revenue-scan/panorama?uf=CE", headers=cabecalho).json()
    assert painel["prevencao"]["pegaria"] == {"aih": 20, "valor": 300000.0}


def test_recarregar_apaga_a_prevencao_da_competencia(fabrica_sessao, tmp_path):
    with fabrica_sessao() as db:
        carregar_competencia(db, "CE", "202605", adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]), tmp_path)
        db.add(SihPrevention(uf="CE", competencia="202605", cnes=HRVJ, n_aih="R1", grupo="PEGARIA", pegaria=True,
                             motivos=[], falhas=[], avisos=[], mensagens=[], referencias={}))
        db.commit()
        carregar_competencia(db, "CE", "202605", adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]), tmp_path)
        assert db.scalar(select(func.count()).select_from(SihPrevention)) == 0


def test_sem_chave_nao_roda_e_falha_do_motor_fica_registrada(fabrica_sessao):
    assert job_prevencao_uf("CE") == {"pulado": "INTERNAL_SERVICE_TOKEN não configurado: a prevenção não roda."}
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        with pytest.raises(MotorIndisponivel):
            rodar(db, "CE", ["202605"], MotorFalso(quebra=True))
        carga = db.execute(select(DataLoad).where(DataLoad.fonte == "FATURASUS")).scalar_one()
        assert carga.status == "FAILED" and "fora do ar" in carga.erro


def test_cliente_manda_a_chave_e_trata_recusa():
    vistos = []

    def responder(request):
        vistos.append(request)
        if request.headers.get("X-Internal-Token") != "k" * 40:
            return httpx.Response(401, json={"detail": "Token de serviço inválido."})
        return httpx.Response(200, json={"resultados": [{"n_aih": "1"}]})

    with FaturaSusNucleo("http://api:8000/", "k" * 40, transport=httpx.MockTransport(responder)) as motor:
        assert motor.avaliar([{"n_aih": "1"}]) == {"resultados": [{"n_aih": "1"}]}
    assert str(vistos[0].url) == "http://api:8000/internal/fatursus/sih/avaliar"
    with FaturaSusNucleo("http://api:8000", "errada", transport=httpx.MockTransport(responder)) as motor:
        with pytest.raises(MotorIndisponivel, match="401"):
            motor.avaliar([{"n_aih": "1"}])


def test_tela_de_dados_enfileira_a_prevencao(app_com_nucleo, monkeypatch):
    admin = {"Authorization": f"Bearer {token(role='platform_admin', tenant_id=None)}"}
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato(platform_admin=True, tenant_id=None,
                                                                          role="platform_admin")))
    assert http.post("/api/revenue-scan/data/prevention", headers=admin, json={"ufs": ["CE"]}).status_code == 409

    monkeypatch.setattr("app.api.routes.dados.get_settings",
                        lambda: replace(get_settings(), internal_service_token="k" * 40))
    monkeypatch.setattr("app.jobs.fila.trabalhos", lambda limite=30: [])
    monkeypatch.setattr("app.jobs.fila.enfileirar_prevencao", lambda uf: f"job-{uf}")
    resposta = http.post("/api/revenue-scan/data/prevention", headers=admin, json={"ufs": ["ce", "PE"]})
    assert resposta.status_code == 202
    assert resposta.json() == {"trabalhos": [{"uf": "CE", "id": "job-CE"}, {"uf": "PE", "id": "job-PE"}], "ignoradas": []}
