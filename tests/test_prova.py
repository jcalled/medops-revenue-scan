"""
A prova AIH por AIH.

O que estes testes travam:
- AIH rejeitada por motivo corrigível e não paga entra na recuperação, com o
  motivo oficial e a ação;
- bloqueio do gestor e motivo sem regra ficam fora, dizendo por quê;
- AIH que voltou aprovada aparece como já recebida;
- cada linha diz de qual arquivo do DATASUS saiu;
- filtros por mês e situação, e escopo do contrato.
"""
import httpx

from app.jobs.recalcular import recalcular
from app.models import DataLoad, SihApprovedAih, SihErrorCode, SihRejection, SihRejectionReason
from tests.conftest import contrato, token
from tests.test_scan_api import HRC, HRVJ, popular


def _dados(fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)  # 30 AIH do HRVJ por 060082 em maio; 2326000000000 voltou aprovada em junho
        db.add_all([
            SihErrorCode(codigo="060082", descricao="QUANTIDADE DE DIÁRIAS SUPERIOR A CAPACIDADE INSTALADA"),
            SihErrorCode(codigo="010003", descricao="NÚMERO DA AIH FORA DE FAIXA"),
            SihRejection(uf="CE", competencia="202606", cnes=HRVJ, n_aih="2326999999901", valor=9_000.0,
                         proc_realizado="0303010010", competencia_aih="202604"),
            SihRejectionReason(uf="CE", competencia="202606", cnes=HRVJ, n_aih="2326999999901", codigo_erro="010003"),
            SihRejection(uf="CE", competencia="202607", cnes=HRVJ, n_aih="2326999999902", valor=4_000.0),
            SihRejectionReason(uf="CE", competencia="202607", cnes=HRVJ, n_aih="2326999999902", codigo_erro="999999"),
            DataLoad(fonte="SIH_RJ", uf="CE", competencia="202605", origem="DOWNLOAD", arquivo="RJCE2605.dbc",
                     checksum="abc123", status="OK"),
            DataLoad(fonte="SIH_ER", uf="CE", competencia="202605", origem="DOWNLOAD", arquivo="ERCE2605.dbc",
                     checksum="def456", status="OK"),
        ])
        db.commit()
        recalcular(db, ufs=["CE"])


def _get(http, caminho, esperado=200):
    resposta = http.get(caminho, headers={"Authorization": f"Bearer {token()}"})
    assert resposta.status_code == esperado, resposta.text
    return resposta.json()


def test_cada_aih_com_situacao_motivo_e_porque(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    corpo = _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas")

    assert corpo["totais"]["RECUPERAR"] == {"aih": 29, "valor": 435_000.0}
    assert corpo["totais"]["JA_RECEBIDA"] == {"aih": 1, "valor": 15_000.0}
    assert corpo["totais"]["FORA_DO_ALCANCE"] == {"aih": 2, "valor": 13_000.0}
    assert corpo["recuperar_por_categoria"] == [
        {"categoria": "CAPACIDADE", "nome": "Diárias acima da capacidade instalada", "aih": 29, "valor": 435_000.0,
         "acima_dos_semelhantes": 435_000.0}]  # os semelhantes não têm AIH rejeitada nesta categoria
    assert corpo["oportunidade_confirmada"] > 0 and "teto" in corpo["leitura"]

    linhas = {l["n_aih"]: l for l in corpo["linhas"]}
    recuperar = linhas["2326000000001"]
    assert recuperar["situacao"] == "RECUPERAR"
    assert recuperar["motivos"] == [{"codigo": "060082", "descricao": "QUANTIDADE DE DIÁRIAS SUPERIOR A CAPACIDADE INSTALADA"}]
    assert "Quantidade de diárias superior a capacidade instalada" in recuperar["porque"]
    assert "leitos SUS no CNES" in recuperar["porque"]
    assert recuperar["fonte"] == {"rejeicao": {"arquivo": "RJCE2605.dbc", "sha256": "abc123"},
                                  "motivo": {"arquivo": "ERCE2605.dbc", "sha256": "def456"}}

    assert linhas["2326000000000"]["situacao"] == "JA_RECEBIDA" and "já entrou" in linhas["2326000000000"]["porque"]
    gestor = linhas["2326999999901"]
    assert gestor["situacao"] == "FORA_DO_ALCANCE" and "Bloqueio do gestor" in gestor["porque"]
    assert gestor["procedimento"] == "0303010010" and gestor["competencia_aih"] == "202604"
    sem_regra = linhas["2326999999902"]
    assert sem_regra["categoria"] == "OUTROS" and "sem descrição na tabela oficial" in sem_regra["porque"]


def test_filtra_por_mes_e_situacao(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    junho = _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas?competencia=202606")
    assert [l["n_aih"] for l in junho["linhas"]] == ["2326999999901"]
    assert junho["totais"]["RECUPERAR"]["aih"] == 29  # totais são do período inteiro
    so_recuperar = _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas?situacao=RECUPERAR")
    assert len(so_recuperar["linhas"]) == 29
    _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas?competencia=202501", 422)
    _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas?situacao=TALVEZ", 422)


def test_valor_do_mes_se_prova_com_as_aih_do_mes(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    prova = _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas")
    # As 29 AIH a recuperar foram processadas em maio: o confirmado inteiro cai em maio.
    assert prova["confirmado_por_mes"] == {"202605": 435_000.0, "202606": 0.0, "202607": 0.0}

    panorama = _get(http, "/api/revenue-scan/panorama?uf=CE")
    hrvj = next(h for h in panorama["ranking"] if h["cnes"] == HRVJ)
    assert hrvj["confirmado_por_mes"] == prova["confirmado_por_mes"]
    assert panorama["confirmado_por_mes"]["202605"] >= 435_000.0
    assert round(sum(panorama["confirmado_por_mes"].values()), 2) <= panorama["impacto_confirmado_total"] + 0.01


def test_prova_respeita_o_escopo(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC]})))
    _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/aih-rejeitadas", 404)
    assert _get(http, f"/api/revenue-scan/hospitals/{HRC}/aih-rejeitadas")["linhas"] == []
