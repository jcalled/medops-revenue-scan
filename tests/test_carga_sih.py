"""
Carga do SIH e o resumo que sai dela.

O que estes testes travam:
- cada AIH conta uma vez por arquivo, e a última linha vence;
- recarregar a mesma competência substitui, não soma;
- falha no meio não deixa competência pela metade, e a carga fica FAILED;
- perda líquida não conta AIH aprovada em algum processamento — nem a que foi
  rejeitada depois de paga;
- o resumo sai por hospitais, por UF ou para o Brasil carregado;
- a carga escolhe as competências que têm RD, RJ e ER, em ordem;
- arquivo baixado é apagado; arquivo de pasta local fica.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters.base import DataSourceAdapter, OrigemArquivo
from app.adapters.datasus import ArquivoIndisponivel
from app.domain.resumo import resumo
from app.jobs.carga_sih import carregar_competencia, carregar_uf, competencias_para_carga
from app.models import DataLoad, SihHospitalMonth, SihHospitalProcedureMonth, SihRejection, SihRejectionReason

HRVJ, HRC = "9672427", "6779522"


class Falso(DataSourceAdapter):
    def __init__(self, tipo: str, dados: dict[str, list[dict]], origem=OrigemArquivo.UPLOAD, quebra=False):
        self.tipo, self.fonte, self.origem = tipo, f"SIH_{tipo}", origem
        self._dados, self._quebra = dados, quebra
        self.caminhos: list[Path] = []

    def competencias_disponiveis(self, uf):
        return sorted(self._dados, reverse=True)

    def obter(self, uf, competencia, destino):
        if competencia not in self._dados:
            raise ArquivoIndisponivel(competencia)
        caminho = Path(destino) / f"{self.tipo}{uf}{competencia[2:]}.dbc"
        caminho.write_bytes(b"dbc")
        self.caminhos.append(caminho)
        return caminho

    def ler(self, caminho):
        if self._quebra:
            raise RuntimeError("arquivo corrompido")
        yield from self._dados["20" + caminho.stem[-4:]]


def aih(n, cnes, valor, diarias=3):
    return {"n_aih": n, "cnes": cnes, "competencia_aih": "202604", "proc_realizado": "0303060212", "valor": valor,
            "dt_internacao": None, "dt_saida": None, "diarias": diarias, "diarias_uti": 0, "permanencia": diarias,
            "marca_uti": "00"}


def erro(n, cnes, codigo):
    return {"n_aih": n, "cnes": cnes, "codigo_erro": codigo, "competencia_aih": "202604"}


def adapters(rd, rj, er, **kw):
    return {"RD": Falso("RD", rd, **kw), "RJ": Falso("RJ", rj, **kw), "ER": Falso("ER", er, **kw)}


MAIO = {
    "RD": {"202605": [aih("A1", HRVJ, 1000.0), aih("A1", HRVJ, 1200.0), aih("A2", HRC, 500.0), aih("B9", HRVJ, 50.0)]},
    "RJ": {"202605": [aih("R1", HRVJ, 7000.0), aih("R2", HRVJ, 3000.0), aih("R2", HRVJ, 3000.0)]},
    "ER": {"202605": [erro("R1", HRVJ, "060082"), erro("R2", HRVJ, "060082"), erro("R2", HRVJ, "010003")]},
}


def test_cada_aih_conta_uma_vez_por_arquivo(fabrica_sessao, tmp_path):
    with fabrica_sessao() as db:
        r = carregar_competencia(db, "CE", "202605", adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]), tmp_path)
        linhas = {h.cnes: h for h in db.execute(select(SihHospitalMonth)).scalars()}
    assert (r.aprovadas, r.rejeitadas, r.motivos, r.hospitais) == (3, 2, 3, 2)
    assert linhas[HRVJ].aih_aprovadas == 2 and float(linhas[HRVJ].valor_aprovado) == 1250.0  # a última linha vence
    assert linhas[HRVJ].aih_rejeitadas == 2 and float(linhas[HRVJ].valor_rejeitado) == 10000.0
    assert linhas[HRC].aih_rejeitadas == 0
    with fabrica_sessao() as db:
        [mix] = db.execute(select(SihHospitalProcedureMonth).where(SihHospitalProcedureMonth.cnes == HRVJ)).scalars()
    assert (mix.proc_realizado, mix.aih, float(mix.valor), mix.permanencia_dias) == ("0303060212", 2, 1250.0, 6)


def test_recarregar_substitui(fabrica_sessao, tmp_path):
    with fabrica_sessao() as db:
        for _ in range(2):
            carregar_competencia(db, "CE", "202605", adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]), tmp_path)
        assert db.query(SihRejection).count() == 2
        assert db.query(SihRejectionReason).count() == 3
        assert [c.status for c in db.query(DataLoad)] == ["OK"] * 6


def test_falha_nao_deixa_competencia_pela_metade(fabrica_sessao, tmp_path):
    with fabrica_sessao() as db:
        carregar_competencia(db, "CE", "202605", adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]), tmp_path)
        quebrado = adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"])
        quebrado["ER"] = Falso("ER", MAIO["ER"], quebra=True)
        with pytest.raises(RuntimeError, match="corrompido"):
            carregar_competencia(db, "CE", "202605", quebrado, tmp_path)
        # A carga boa anterior continua inteira.
        assert db.query(SihRejection).count() == 2 and db.query(SihHospitalMonth).count() == 2
        falhas = db.query(DataLoad).filter(DataLoad.status == "FAILED").all()
        assert len(falhas) == 3 and "corrompido" in falhas[0].erro


def test_perda_liquida_nao_conta_aih_que_foi_paga(fabrica_sessao, tmp_path):
    junho = {
        "RD": {"202606": [aih("R1", HRVJ, 7000.0)]},          # R1, rejeitada em maio, voltou aprovada
        "RJ": {"202606": [aih("B9", HRVJ, 50.0)]},             # B9 aprovada em maio, rejeitada em junho
        "ER": {"202606": [erro("B9", HRVJ, "040008")]},
    }
    with fabrica_sessao() as db:
        carregar_competencia(db, "CE", "202605", adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]), tmp_path)
        carregar_competencia(db, "CE", "202606", adapters(junho["RD"], junho["RJ"], junho["ER"]), tmp_path)
        corpo = resumo(db, cnes=[HRVJ, HRC])
        estado, brasil = resumo(db, uf="CE"), resumo(db)
        outra_uf = resumo(db, uf="PE")

    assert corpo["competencias"] == ["202605", "202606"]
    # R1 voltou aprovada em junho; B9 foi paga em maio e rejeitada na
    # reapresentação de junho. Só R2 é perda.
    assert corpo["rejeitadas_unicas"] == 3 and corpo["voltaram_aprovadas"] == 2
    assert corpo["perda_liquida_aih"] == 1 and corpo["perda_liquida_valor"] == 3000.0
    assert estado["total"] == brasil["total"] == corpo["total"]
    assert outra_uf["total"]["aih_aprovadas"] == 0 and outra_uf["hospitais"] == []
    hrvj = next(h for h in corpo["hospitais"] if h["cnes"] == HRVJ)
    assert hrvj["total"]["aih_rejeitadas"] == 3 and hrvj["rejeitado_mes"] == 5025.0
    assert hrvj["motivos"][0] == {"codigo": "060082", "descricao": None, "aih": 2}


def test_escolhe_competencias_com_as_tres_fontes_em_ordem():
    tres = adapters({"202605": [], "202606": [], "202607": []}, {"202606": [], "202607": []}, {"202606": [], "202607": []})
    assert competencias_para_carga(tres, "CE", 3) == ["202606", "202607"]
    assert competencias_para_carga(tres, "CE", 1) == ["202607"]


def test_arquivo_baixado_e_apagado_e_local_fica(fabrica_sessao, tmp_path):
    baixados = adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"], origem=OrigemArquivo.DOWNLOAD)
    with fabrica_sessao() as db:
        carregar_competencia(db, "CE", "202605", baixados, tmp_path)
    caminhos = [c for a in baixados.values() for c in a.caminhos]
    assert len(caminhos) == 3 and not any(c.exists() for c in caminhos)

    locais = adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"])
    with fabrica_sessao() as db:
        carregar_competencia(db, "CE", "202605", locais, tmp_path)
    assert all(c.exists() for a in locais.values() for c in a.caminhos)


def test_carga_da_uf_pega_as_competencias_disponiveis(fabrica_sessao):
    with fabrica_sessao() as db:
        resultados = carregar_uf(db, "ce", adapters=adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]))
    assert [(r.uf, r.competencia) for r in resultados] == [("CE", "202605")]


def test_todas_as_ufs_do_brasil():
    from app.jobs.carga_sih import ufs_do_argumento

    todas = ufs_do_argumento("todas")
    assert len(todas) == 27 and {"CE", "SP", "DF", "RR"} <= set(todas)
    assert ufs_do_argumento("ce, pe") == ["CE", "PE"]
    with pytest.raises(ValueError, match="UF"):
        ufs_do_argumento("CE,XX")


def test_uf_sem_competencia_publicada(fabrica_sessao):
    with fabrica_sessao() as db, pytest.raises(ArquivoIndisponivel, match="CE"):
        carregar_uf(db, "CE", adapters=adapters({}, {}, {}))
