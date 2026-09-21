"""
Relatório de recuperação por hospital e a carga do DATASUS na hora.

O que estes testes travam:
- cada AIH cai num grupo só: recuperada (aprovada depois da rejeição), a recuperar, depende do gestor, perdida;
- recuperada entra com o mês da aprovação, e o percentual incide sobre ela — só a partir do início do contrato;
- o percentual sobre o que falta recuperar é estimativa, à parte;
- por hospital e por mês de rejeição, com os principais motivos e o que o botão do FaturaSUS corrige;
- sem organização nem hospitais, o relatório é recusado;
- atualizar é da administração da plataforma e só enfileira os meses publicados que faltam, por UF.
"""
import httpx

from app.models import SihHospitalMonth
from app.seed.kits_motivo import aplicar
from tests.conftest import contrato, token
from tests.test_dados import ADMIN, _admin, fila_falsa  # noqa: F401 — fixture
from tests.test_kit import _dados, _get
from tests.test_scan_api import HRC, HRVJ


def test_grupos_por_hospital_e_mes(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    rel = _get(http, f"/api/revenue-scan/recovery-report?cnes={HRVJ},{HRC}&referencia=202610")

    t = rel["total"]
    assert t["rejeitadas"] == {"aih": 38, "valor": 462600.0}
    assert t["RECUPERADA"] == {"aih": 1, "valor": 15000.0}
    assert t["A_RECUPERAR"] == {"aih": 3, "valor": 3700.0}          # profissional x2 e habilitação
    assert t["DEPENDE_GESTOR"] == {"aih": 3, "valor": 5300.0}       # gestor e investigar
    assert t["PERDIDA"] == {"aih": 31, "valor": 438600.0}           # capacidade e prazo
    assert t["botao"] == {"aih": 3, "valor": 2500.0}                # 060109 é do botão
    assert t["vence_neste_mes"] == {"aih": 1, "valor": 2000.0}
    assert rel["recuperado_por_mes"] == [{"competencia": "202606", "aih": 1, "valor": 15000.0}]
    assert rel["taxa"] == {"sobre_recuperado": 2250.0, "recuperado_cobravel": 15000.0,
                           "estimada_sobre_a_recuperar": 555.0, "estimada_com_gestor": 1350.0}

    [hrvj, hrc] = rel["hospitais"]
    assert (hrvj["cnes"], hrc["cnes"]) == (HRVJ, HRC)
    assert [m["competencia"] for m in hrvj["meses"]] == ["202605", "202606"]
    assert hrvj["meses"][0]["RECUPERADA"]["aih"] == 1 and hrvj["meses"][0]["PERDIDA"]["aih"] == 29
    assert [(m["codigo"], m["aih"], m["valor"]) for m in hrvj["motivos"]] == [
        ("010003", 1, 4000.0), ("060120", 1, 2000.0), ("060109", 2, 1800.0), ("999999", 1, 500.0)]
    assert hrc["total"]["A_RECUPERAR"] == {"aih": 1, "valor": 700.0} and hrc["taxa"]["sobre_recuperado"] == 0


def test_inicio_do_contrato_e_percentual(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    rel = _get(http, f"/api/revenue-scan/recovery-report?cnes={HRVJ}&referencia=202610&inicio=202607&percentual=20")
    # Aprovada em jun, antes do início: fica no relatório, mas não é cobrável.
    assert rel["total"]["RECUPERADA"]["aih"] == 1
    assert rel["taxa"]["recuperado_cobravel"] == 0 and rel["taxa"]["sobre_recuperado"] == 0
    assert rel["taxa"]["estimada_sobre_a_recuperar"] == 600.0      # 20% de 3.000


def test_sem_selecao_e_recusado(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    resposta = http.get("/api/revenue-scan/recovery-report", headers={"Authorization": f"Bearer {token()}"})
    assert resposta.status_code == 422


def test_atualizar_so_os_meses_que_faltam(app_com_nucleo, fabrica_sessao, fila_falsa, monkeypatch):  # noqa: F811
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        carregados = set(db.query(SihHospitalMonth.competencia).filter(SihHospitalMonth.uf == "CE").distinct().all())

    class Falso:
        def competencias_disponiveis(self, uf):
            return ["202605", "202606", "202607", "202608"]

    monkeypatch.setattr("app.jobs.carga_sih.adapters_padrao", lambda pasta_local=None: {t: Falso() for t in ("RD", "RJ", "ER")})
    http = _admin(app_com_nucleo)

    cliente = http.post("/api/revenue-scan/recovery-report/refresh", json={"cnes": [HRVJ]},
                        headers={"Authorization": f"Bearer {token()}"})
    assert cliente.status_code == 403 and fila_falsa["cargas"] == []

    corpo = http.post("/api/revenue-scan/recovery-report/refresh", json={"cnes": [HRVJ], "meses": 4}, headers=ADMIN).json()
    [ce] = corpo["ufs"]
    faltando = [c for c in ["202605", "202606", "202607", "202608"] if (c,) not in carregados]
    assert ce["uf"] == "CE" and ce["faltando"] == faltando and ce["situacao"] == "ENFILEIRADA"
    assert fila_falsa["cargas"] == [("CE", faltando, len(faltando))]

    # UF já na fila não duplica.
    fila_falsa["trabalhos"].append({"tipo": "CARGA", "status": "RODANDO", "ufs": ["CE"]})
    corpo = http.post("/api/revenue-scan/recovery-report/refresh", json={"cnes": [HRVJ], "meses": 4}, headers=ADMIN).json()
    assert corpo["ufs"][0]["situacao"] == "JA_NA_FILA" and len(fila_falsa["cargas"]) == 1


def test_pacote_de_correcao_por_motivo(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        aplicar(db)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    pacote = _get(http, f"/api/revenue-scan/recovery-report/package?cnes={HRVJ},{HRC}&referencia=202610")

    [hrvj, hrc] = pacote["hospitais"]
    assert hrvj["cnes"] == HRVJ and hrvj["total"] == {"aih": 5, "valor": 8300.0}   # só o que ainda está no prazo
    assert hrvj["vence_neste_mes"] == {"aih": 1, "valor": 2000.0}
    assert [m["codigo"] for m in hrvj["motivos"]] == ["010003", "060120", "060109", "999999"]
    profissional = hrvj["motivos"][2]
    assert profissional["kit"]["passos"] and profissional["kit"]["fonte"]              # o que fazer e a regra
    assert [a["n_aih"] for a in profissional["aih"]] == ["P1", "S1"]                   # com prazo antes, sem data no fim
    assert all(a["botao_faturasus"] for a in profissional["aih"])
    assert hrc["motivos"][0]["aih"][0]["n_aih"] == "B1"

    linhas = pacote["planilha"]
    assert len(linhas) == 6 and {l["n_aih"] for l in linhas} == {"P1", "H1", "G1", "O1", "S1", "B1"}
    assert next(l for l in linhas if l["n_aih"] == "P1")["botao_faturasus"].startswith("sim")
    # Capacidade e prazo vencido não entram no pacote: não há o que reapresentar.
    assert not {"V1", "Z1"} & {l["n_aih"] for l in linhas}


def test_ranking_por_quanto_falta_e_por_quanto_ja_recupera(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    ranking = _get(http, "/api/revenue-scan/recovery-report/ranking?uf=CE&referencia=202610")
    assert [h["cnes"] for h in ranking["hospitais"]] == [HRVJ, HRC]          # R$ 3.000 a recuperar contra R$ 700
    hrvj = ranking["hospitais"][0]
    assert hrvj["a_recuperar"] == {"aih": 2, "valor": 3000.0} and hrvj["recuperado"]["valor"] == 15000.0
    assert hrvj["taxa_recuperacao"] == round(15000 / 461900, 4)
    assert hrvj["motivo_principal"]["codigo"] == "010003"

    por_recuperado = _get(http, "/api/revenue-scan/recovery-report/ranking?uf=CE&referencia=202610&ordem=recuperado")
    assert por_recuperado["hospitais"][0]["cnes"] == HRVJ
    assert http.get("/api/revenue-scan/recovery-report/ranking?ordem=xyz",
                    headers={"Authorization": f"Bearer {token()}"}).status_code == 422


def test_simulacao_faturasus_no_dado_publico(app_com_nucleo, fabrica_sessao):
    from app.models import SihPrevention

    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        aplicar(db)
        db.add(SihPrevention(uf="CE", competencia="202606", cnes=HRVJ, n_aih="P1", grupo="PEGARIA", pegaria=True,
                             motivos=[], falhas=["SUS_CBO_PROC_INCOMP"], avisos=[],
                             mensagens=[{"code": "SUS_CBO_PROC_INCOMP", "status": "FAIL", "message": "CBO 225125 não executa o procedimento."}]))
        db.add(SihPrevention(uf="CE", competencia="202606", cnes=HRVJ, n_aih="H1", grupo="PRECISA_ARQUIVO", pegaria=False,
                             motivos=[], falhas=[], avisos=[], mensagens=[]))
        db.commit()
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))

    rel = _get(http, f"/api/revenue-scan/recovery-report?cnes={HRVJ},{HRC}&referencia=202610")
    simulacao = {s["grupo"]: (s["aih"], s["valor"]) for s in rel["simulacao"]}
    # Só o que ainda dá para recuperar entra; capacidade e prazo vencido ficam de fora.
    assert simulacao == {"PEGARIA": (1, 1000.0), "PRECISA_ARQUIVO": (1, 2000.0), "SEM_AVALIACAO": (4, 6000.0)}
    assert rel["simulacao_nomes"]["PEGARIA"] == "O FaturaSUS já aponta o erro"

    pacote = _get(http, f"/api/revenue-scan/recovery-report/package?cnes={HRVJ}&referencia=202610")
    p1 = next(a for m in pacote["hospitais"][0]["motivos"] for a in m["aih"] if a["n_aih"] == "P1")
    assert p1["faturasus"] == {"grupo": "PEGARIA", "regras": ["SUS_CBO_PROC_INCOMP"], "mensagem": "CBO 225125 não executa o procedimento."}
    assert next(l for l in pacote["planilha"] if l["n_aih"] == "H1")["faturasus_aponta"] == "Precisa do arquivo do hospital"


def test_apac_acima_do_teto_fica_a_parte(app_com_nucleo, fabrica_sessao):
    from app.models import SiaApacMonth

    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        db.add(SiaApacMonth(uf="CE", competencia="202606", cnes=HRVJ, linhas=3, valor_produzido=1850, valor_aprovado=1000,
                            valor_nao_aprovado=850, valor_teto=800,
                            ocorrencias={"5O": {"linhas": 1, "valor": 800.0, "nome": "Ultrapassou o teto financeiro"},
                                         "4Q": {"linhas": 1, "valor": 50.0, "nome": "Procedimento sem valor unitário"}},
                            procedimentos=[{"procedimento": "0304050024", "valor": 800.0}]))
        db.add(SiaApacMonth(uf="CE", competencia="201901", cnes=HRVJ, linhas=1, valor_produzido=5, valor_aprovado=0,
                            valor_nao_aprovado=5, valor_teto=5, ocorrencias={}, procedimentos=[]))  # fora do período
        db.commit()
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    rel = _get(http, f"/api/revenue-scan/recovery-report?cnes={HRVJ},{HRC}&referencia=202610")
    apac = next(h for h in rel["hospitais"] if h["cnes"] == HRVJ)["apac"]
    assert (apac["produzido"], apac["nao_aprovado"], apac["teto"]) == (1850.0, 850.0, 800.0)
    assert apac["ocorrencias"][0]["codigo"] == "5O" and apac["meses"] == ["202606"]
    assert rel["total"]["A_RECUPERAR"]["valor"] == 3700.0          # a APAC não entra no recuperável
    assert rel["apac"]["teto"] == 800.0
    assert next(h for h in rel["hospitais"] if h["cnes"] == HRC)["apac"] is None


def test_como_deixar_de_perder(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    rel = _get(http, f"/api/revenue-scan/recovery-report?cnes={HRVJ}&referencia=202610")
    [hrvj] = rel["hospitais"]
    prevenir = {p["categoria"]: p for p in hrvj["prevenir"]}
    capacidade = prevenir["CAPACIDADE"]
    assert hrvj["prevenir"][0]["categoria"] == "CAPACIDADE"                       # a maior perda vem primeiro
    assert capacidade["rejeitado"] == {"aih": 31, "valor": 453000.0}             # 30 de maio (uma voltou) e a V1
    assert capacidade["nao_volta"] == {"aih": 30, "valor": 438000.0}
    assert capacidade["media_mensal"] == 226500.0                                  # dois meses com rejeição
    assert "CNES" in capacidade["como_evitar"] and capacidade["motivos"] == ["060082"]
    assert "PRAZO" in prevenir and prevenir["PRAZO"]["nao_volta"]["valor"] == 600.0
    assert set(hrvj["capacidade"]) >= {"leitos_sus", "limite_diarias_mes", "diarias_mes", "ocupacao"}
