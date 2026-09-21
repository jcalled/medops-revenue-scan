"""
APAC do SIA (arquivo PA).

O que estes testes travam:
- só APAC entra (PA_DOCORIG P ou S); BPA fica de fora;
- o não aprovado sai por ocorrência oficial (CODOCO.CNV) e o teto fica separado do resto;
- UF grande partida em a, b, c é lida inteira; competência sem arquivo publicado fica de fora;
- recarregar a competência substitui, e a carga fica registrada.
"""
from pathlib import Path

from app.adapters import sia
from app.jobs.carga_sia import carregar_apac
from app.models import DataLoad, SiaApacMonth


def _linha(cnes, doc, pro, apr, codoco="1", flqt="K", proc="0304050024"):
    return {"PA_CODUNI": cnes, "PA_DOCORIG": doc, "PA_VALPRO": pro, "PA_VALAPR": apr, "PA_CODOCO": codoco,
            "PA_FLQT": flqt, "PA_PROC_ID": proc}


LINHAS = [
    _linha("2723166", "P", 1000.0, 1000.0),
    _linha("2723166", "P", 800.0, 0.0, "5", "O"),                    # teto financeiro
    _linha("2723166", "S", 50.0, 0.0, "4", "Q", proc="0202010660"),  # sem valor unitário: não é teto
    _linha("2723166", "I", 999.0, 0.0, "5", "O"),                    # BPA-I: fora
    _linha("0000000", "P", 10.0, 0.0, "5", "O"),                     # sem CNES: fora
]


def test_agrega_por_hospital_e_ocorrencia():
    h = sia.agregar(LINHAS)["2723166"]
    assert (h["linhas"], h["valor_produzido"], h["valor_aprovado"]) == (3, 1850.0, 1000.0)
    assert (h["valor_nao_aprovado"], h["valor_teto"]) == (850.0, 800.0)
    assert h["ocorrencias"]["5O"] == {"linhas": 1, "valor": 800.0, "nome": "Ultrapassou o teto financeiro"}
    assert h["procedimentos"][0] == {"procedimento": "0304050024", "valor": 800.0}


def test_nomes_dos_arquivos():
    nomes = ["PACE2607.dbc", "PASP2607a.dbc", "PASP2607b.dbc", "PASP2606.dbc", "RDCE2607.dbc"]
    assert sia.arquivos_da_competencia(nomes, "SP", "202607") == ["PASP2607a.dbc", "PASP2607b.dbc"]
    assert sia.competencias_da_listagem(nomes, "SP") == ["202606", "202607"]


def test_carga_substitui_e_registra(fabrica_sessao, tmp_path):
    partes = {"PACE2607a.dbc": LINHAS[:2], "PACE2607b.dbc": LINHAS[2:]}

    def baixar(pasta, nome, destino):
        caminho = Path(destino) / nome
        caminho.write_text("x")
        return caminho

    def ler(caminho):
        return iter(partes[Path(caminho).name])

    with fabrica_sessao() as db:
        for _ in range(2):  # a segunda carga substitui a primeira
            assert carregar_apac(db, "CE", ["202607", "202608"], listar=lambda p: list(partes), baixar=baixar, ler=ler) == {"202607": 1}
        [linha] = db.query(SiaApacMonth).all()
        assert (linha.cnes, float(linha.valor_teto)) == ("2723166", 800.0)
        assert [c.status for c in db.query(DataLoad).filter_by(fonte="SIA_PA")] == ["OK", "OK"]
