"""
Leitura das fontes: nomes de arquivo, colunas do SIH, motivos de erro e CNES.

O que estes testes travam:
- a competência vem do nome do arquivo, e só dos arquivos do tipo e UF pedidos;
- linha do DBF sem AIH ou sem CNES não entra; CNES ganha zeros à esquerda;
- código de erro lido como número da planilha volta a ter seis dígitos;
- a API do CNES devolve o nome fantasia e a UF pela sigla; CNES inexistente é None.
"""
import io
import zipfile
from datetime import date

import httpx
import pytest
from openpyxl import Workbook

from app.adapters.cnes_api import CnesDadosAbertos
from app.adapters.datasus import ArquivoIndisponivel
from app.adapters.sih import SihAdapter, competencias_da_listagem
from app.adapters.sih_erros import ler_codigos_de_erro


def test_competencias_da_listagem():
    nomes = ["RDCE2605.dbc", "RDCE2607.DBC", "RJCE2606.dbc", "RDPE2607.dbc", "leia-me.txt", "RDCE2606.dbc"]
    assert competencias_da_listagem(nomes, "RD", "CE") == ["202607", "202606", "202605"]
    assert competencias_da_listagem(nomes, "RJ", "CE") == ["202606"]


def test_aih_normalizada_e_linha_invalida_descartada(tmp_path):
    linhas = [
        {"N_AIH": "2326102423084", "CNES": "86673", "ANO_CMPT": "2026", "MES_CMPT": "07", "PROC_REA": "0407020039",
         "VAL_TOT": 526.33, "DT_INTER": "20260719", "DT_SAIDA": "20260722", "QT_DIARIAS": 3, "UTI_MES_TO": 0,
         "DIAS_PERM": 3, "MARCA_UTI": "00"},
        {"N_AIH": "", "CNES": "2785900"},
        {"N_AIH": "2326102423085", "CNES": "0000000"},
    ]
    adapter = SihAdapter("RD", ler=lambda caminho: iter(linhas))
    [aih] = list(adapter.ler(tmp_path / "RDCE2607.dbc"))
    assert aih["cnes"] == "0086673" and aih["competencia_aih"] == "202607"
    assert aih["valor"] == 526.33 and aih["dt_internacao"] == date(2026, 7, 19) and aih["diarias"] == 3


def test_motivo_de_rejeicao_normalizado(tmp_path):
    linhas = [{"AIH": "2326170013662", "CNES": "2526638", "CO_ERRO": "060082", "ANO": "2026", "MES": "5"}]
    [motivo] = list(SihAdapter("ER", ler=lambda caminho: iter(linhas)).ler(tmp_path / "x.dbc"))
    assert motivo == {"n_aih": "2326170013662", "cnes": "2526638", "codigo_erro": "060082", "competencia_aih": "202605"}


def test_pasta_local_acha_o_arquivo_sem_diferenciar_maiusculas(tmp_path):
    (tmp_path / "RJCE2606.DBC").write_bytes(b"x")
    adapter = SihAdapter("RJ", pasta_local=tmp_path)
    assert adapter.competencias_disponiveis("ce") == ["202606"]
    assert adapter.obter("CE", "202606", tmp_path).name == "RJCE2606.DBC"
    with pytest.raises(ArquivoIndisponivel):
        adapter.obter("CE", "202607", tmp_path)


def test_uf_desconhecida_e_recusada():
    with pytest.raises(ValueError, match="UF"):
        SihAdapter("RD").nome_arquivo("XX", "202607")


def test_codigos_de_erro_da_tab_sih(tmp_path):
    livro = Workbook()
    folha = livro.active
    folha.append(["CO_TAB", "CO_ITEM", "DT_CMPT_INI", "DT_CMPT_FIM", "DS_DESCRICAO"])
    folha.append(["0024", "0001", "200701", "999999", "DUPLICIDADE"])
    folha.append(["0027", 60082, "200701", "999999", "QUANTIDADE DE DIÁRIAS   SUPERIOR A CAPACIDADE INSTALADA  "])
    folha.append(["0027", "010003", "200701", "999999", "NÚMERO DA AIH FORA DE FAIXA"])
    planilha = io.BytesIO()
    livro.save(planilha)
    caminho = tmp_path / "TAB_SIH.zip"
    with zipfile.ZipFile(caminho, "w") as pacote:
        pacote.writestr("Docs/erroebloqueio.xlsx", planilha.getvalue())

    assert ler_codigos_de_erro(caminho) == {
        "060082": "QUANTIDADE DE DIÁRIAS SUPERIOR A CAPACIDADE INSTALADA",
        "010003": "NÚMERO DA AIH FORA DE FAIXA",
    }


def test_api_do_cnes():
    corpo = {"codigo_cnes": 2785900, "nome_fantasia": "HOSPITAL GERAL DR WALDEMAR ALCANTARA",
             "nome_razao_social": "SECRETARIA DA SAUDE DO ESTADO DO CEARA", "codigo_uf": 23,
             "codigo_municipio": 230440, "numero_cnpj_entidade": "07954571000104",
             "descricao_natureza_juridica_estabelecimento": "1023", "descricao_esfera_administrativa": "ESTADUAL",
             "codigo_tipo_unidade": 5}
    tentativas = []

    def responder(request: httpx.Request) -> httpx.Response:
        tentativas.append(request.url.path)
        if request.url.path.endswith("9999999"):
            return httpx.Response(404)
        if len(tentativas) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=corpo)

    with CnesDadosAbertos(transport=httpx.MockTransport(responder), espera=lambda s: None) as api:
        dados = api.estabelecimento("2785900")
        assert api.estabelecimento("9999999") is None
    assert dados["nome_fantasia"] == "HOSPITAL GERAL DR WALDEMAR ALCANTARA"
    assert dados["uf"] == "CE" and dados["codigo_municipio"] == "230440" and dados["cnpj_entidade"] == "07954571000104"
    assert tentativas[:2] == ["/cnes/estabelecimentos/2785900"] * 2  # 503 é tentado de novo
