"""
Leitos e habilitações do CNES.

O que estes testes travam:
- a competência vem do nome do arquivo, e sem pedido é a mais recente;
- leito repetido no arquivo soma; habilitação repetida conta uma vez;
- recarregar a mesma competência substitui;
- a carga do SIH grava o mix de procedimentos do hospital.
"""
from pathlib import Path

from app.adapters.base import DataSourceAdapter, OrigemArquivo
from app.adapters.cnes_arquivos import CnesArquivoAdapter
from app.jobs.carga_cnes import carregar_cnes_uf
from app.models import CnesBed, CnesEnablement, DataLoad


class Falso(DataSourceAdapter):
    def __init__(self, tipo, dados):
        self.tipo, self.fonte, self.origem, self._dados = tipo, f"CNES_{tipo}", OrigemArquivo.UPLOAD, dados

    def competencias_disponiveis(self, uf):
        return sorted(self._dados, reverse=True)

    def obter(self, uf, competencia, destino):
        caminho = Path(destino) / f"{self.tipo}{uf}{competencia[2:]}.dbc"
        caminho.write_bytes(b"x")
        return caminho

    def ler(self, caminho):
        yield from self._dados["20" + caminho.stem[-4:]]


def leito(cnes, codigo, tipo, sus, existentes=None):
    return {"cnes": cnes, "codigo_leito": codigo, "tipo_leito": tipo, "qt_existente": existentes or sus, "qt_sus": sus}


def habilitacao(cnes, codigo, inicio="201501", fim=None):
    return {"cnes": cnes, "habilitacao": codigo, "competencia_inicio": inicio, "competencia_fim": fim}


def test_leitos_somam_e_habilitacoes_nao_repetem(fabrica_sessao):
    adapters = {
        "LT": Falso("LT", {"202606": [leito("9672427", "33", "2", 10)],
                           "202607": [leito("9672427", "33", "2", 20), leito("9672427", "33", "2", 2),
                                      leito("9672427", "75", "3", 30)]}),
        "HB": Falso("HB", {"202607": [habilitacao("9672427", "2601"), habilitacao("9672427", "2601")]}),
    }
    with fabrica_sessao() as db:
        for _ in range(2):
            resultado = carregar_cnes_uf(db, "CE", adapters=adapters)
        assert resultado == {"LT": ("202607", 2), "HB": ("202607", 1)}
        leitos = {(l.codigo_leito, l.tipo_leito): l.qt_sus for l in db.query(CnesBed).all()}
        assert leitos == {("33", "2"): 22, ("75", "3"): 30}
        assert db.query(CnesEnablement).count() == 1
        assert {c.status for c in db.query(DataLoad).all()} == {"OK"}


def test_arquivos_do_cnes_normalizados(tmp_path):
    lt = CnesArquivoAdapter("LT", ler=lambda c: iter([
        {"CNES": "9672427", "CODLEITO": "3", "TP_LEITO": "1", "QT_EXIST": 30, "QT_SUS": 28},
        {"CNES": "", "CODLEITO": "33"},
    ]))
    assert list(lt.ler(tmp_path / "LTCE2607.dbc")) == [
        {"cnes": "9672427", "codigo_leito": "03", "tipo_leito": "1", "qt_existente": 30, "qt_sus": 28}]
    hb = CnesArquivoAdapter("HB", ler=lambda c: iter([
        {"CNES": "86673", "SGRUPHAB": "2601", "CMPT_INI": "201709", "CMPT_FIM": "999999"}]))
    assert list(hb.ler(tmp_path / "HBCE2607.dbc")) == [
        {"cnes": "0086673", "habilitacao": "2601", "competencia_inicio": "201709", "competencia_fim": "999999"}]
    (tmp_path / "LTCE2607.dbc").write_bytes(b"x")
    (tmp_path / "LTCE2606.dbc").write_bytes(b"x")
    assert CnesArquivoAdapter("LT", pasta_local=tmp_path).competencias_disponiveis("CE") == ["202607", "202606"]
