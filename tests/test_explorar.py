"""
Explorar hospitais e montar recortes.

O que estes testes travam:
- natureza jurídica separa prefeitura, governo estadual, federal, filantrópico e privado;
- o score guarda UF, município, natureza, gestão e porte para filtrar sem recalcular;
- lista, filtros e panorama respeitam o escopo do contrato;
- o panorama sai para qualquer recorte: natureza, seleção de CNES, organização;
- municípios do IBGE dão nome ao código do CNES.
"""
import httpx
import pytest
from sqlalchemy import select

from app.adapters.ibge import municipios_ibge
from app.domain.classificacao import gestao, grupo_natureza
from app.jobs.carga_ibge import carregar_municipios
from app.jobs.recalcular import recalcular
from app.models import Establishment, HospitalScore, ManagementOrganization
from tests.conftest import contrato, token
from tests.test_scan_api import HRC, HRVJ, PARES, popular

PREFEITURAS = PARES[:3]


@pytest.mark.parametrize("codigo,grupo", [
    ("1244", "MUNICIPAL"), ("1031", "MUNICIPAL"), ("1120", "MUNICIPAL"),
    ("1023", "ESTADUAL"), ("1236", "ESTADUAL"), ("1104", "FEDERAL"), ("1325", "OUTRO_PUBLICO"),
    ("2011", "EMPRESA_PUBLICA"), ("2062", "PRIVADO"), ("3999", "FILANTROPICO"), ("3069", "FILANTROPICO"),
    (None, "NAO_INFORMADA"), ("xx", "NAO_INFORMADA"),
])
def test_natureza_juridica(codigo, grupo):
    assert grupo_natureza(codigo) == grupo


def test_gestao():
    assert (gestao("municipal"), gestao("ESTADUAL"), gestao("PRIVADA"), gestao(None)) == ("MUNICIPAL", "ESTADUAL", None, None)


def _dados(fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)
        for cnes in PREFEITURAS:
            e = db.get(Establishment, cnes)
            e.natureza_juridica, e.esfera, e.codigo_municipio = "1244", "MUNICIPAL", "230440"
        for cnes in (HRVJ, HRC):
            db.get(Establishment, cnes).esfera = "ESTADUAL"
        carregar_municipios(db, [{"codigo": "2304400", "codigo_cnes": "230440", "nome": "Fortaleza", "uf": "CE"}])
        recalcular(db, ufs=["CE"])
        return db.execute(select(ManagementOrganization.id).where(ManagementOrganization.sigla == "ISGH")).scalar()


def _get(http, caminho):
    resposta = http.get(caminho, headers={"Authorization": f"Bearer {token()}"})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def test_score_guarda_os_atributos_do_hospital(fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        s = db.execute(select(HospitalScore).where(HospitalScore.cnes == PREFEITURAS[0])).scalar_one()
        hrvj = db.execute(select(HospitalScore).where(HospitalScore.cnes == HRVJ)).scalar_one()
    assert (s.uf, s.codigo_municipio, s.natureza_grupo, s.gestao, s.porte) == (
        "CE", "230440", "MUNICIPAL", "MUNICIPAL", "51 a 150 leitos")
    assert hrvj.natureza_grupo == "ESTADUAL" and float(hrvj.impacto_confirmado) > 0
    assert float(hrvj.impacto_confirmado) + float(hrvj.impacto_sinais) == pytest.approx(float(hrvj.impacto_estimado))


def test_lista_filtra_ordena_e_pagina(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    todos = _get(http, "/api/revenue-scan/hospitals")
    assert todos["total"] == 9 and todos["itens"][0]["cnes"] == HRVJ
    assert todos["itens"][0]["organizacoes"] == ["ISGH"]

    prefeituras = _get(http, "/api/revenue-scan/hospitals?uf=ce&natureza=MUNICIPAL&ordem=nome")
    assert prefeituras["total"] == 3
    assert {i["cnes"] for i in prefeituras["itens"]} == set(PREFEITURAS)
    assert prefeituras["itens"][0]["municipio"] == "Fortaleza/CE"
    assert prefeituras["itens"][0]["natureza"] == "Prefeitura (público municipal)"

    assert _get(http, "/api/revenue-scan/hospitals?gestao=ESTADUAL")["total"] == 2
    assert _get(http, f"/api/revenue-scan/hospitals?q={HRC}")["itens"][0]["cnes"] == HRC
    assert _get(http, "/api/revenue-scan/hospitals?q=hospital%2023000")["total"] == 7
    pagina = _get(http, "/api/revenue-scan/hospitals?por_pagina=4&pagina=3")
    assert pagina["total"] == 9 and len(pagina["itens"]) == 1


def test_filtros_disponiveis(app_com_nucleo, fabrica_sessao):
    org_id = _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    corpo = _get(http, "/api/revenue-scan/filters?uf=CE")
    assert corpo["hospitais"] == 9 and corpo["ufs"] == [{"valor": "CE", "rotulo": "CE", "hospitais": 9}]
    assert {n["valor"]: n["hospitais"] for n in corpo["naturezas"]} == {"MUNICIPAL": 3, "ESTADUAL": 6}
    assert corpo["municipios"] == [{"codigo": "230440", "nome": "Fortaleza/CE", "hospitais": 3}]
    assert corpo["organizacoes"] == [{"id": org_id, "sigla": "ISGH", "nome": "Instituto de Saúde e Gestão Hospitalar",
                                      "uf": "CE", "hospitais": 2, "hospitais_cadastrados": 8}]


def test_panorama_de_qualquer_recorte(app_com_nucleo, fabrica_sessao):
    org_id = _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))

    prefeituras = _get(http, "/api/revenue-scan/panorama?uf=CE&natureza=MUNICIPAL")
    assert prefeituras["recorte"]["titulo"] == "Prefeitura (público municipal) · CE"
    assert prefeituras["hospitais_analisados"] == 3 and prefeituras["rejeicao"]["total"]["aih_aprovadas"] == 2700

    selecao = _get(http, f"/api/revenue-scan/panorama?cnes={HRVJ},{HRC}")
    assert selecao["recorte"]["titulo"] == "Seleção de 2 hospitais"
    assert [h["cnes"] for h in selecao["ranking"]] == [HRVJ, HRC]
    assert selecao["rejeicao"]["perda_liquida_aih"] == 29

    oss = _get(http, f"/api/revenue-scan/panorama?organizacao={org_id}")
    assert oss["recorte"]["titulo"] == "Instituto de Saúde e Gestão Hospitalar (ISGH)"
    assert oss["hospitais_do_recorte"] == 8 and oss["hospitais_analisados"] == 2 and len(oss["sem_scan"]) == 6

    por_valor = _get(http, "/api/revenue-scan/panorama?ordem=confirmado")
    valores = [h["impacto_confirmado"] for h in por_valor["ranking"]]
    assert valores == sorted(valores, reverse=True) and por_valor["ranking"][0]["cnes"] == HRVJ

    tudo = _get(http, "/api/revenue-scan/panorama?limite_ranking=3")
    assert tudo["recorte"]["titulo"] == "Todos os hospitais carregados"
    assert tudo["hospitais_analisados"] == 9 and len(tudo["ranking"]) == 3 and tudo["ranking_limitado"] is True


def test_escopo_do_contrato_vale_para_explorar(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC, PREFEITURAS[0]]})))
    assert {i["cnes"] for i in _get(http, "/api/revenue-scan/hospitals")["itens"]} == {HRC, PREFEITURAS[0]}
    assert _get(http, "/api/revenue-scan/panorama?natureza=ESTADUAL")["hospitais_analisados"] == 1
    assert _get(http, "/api/revenue-scan/filters")["hospitais"] == 2

    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"organizations": ["ISGH"]})))
    assert {i["cnes"] for i in _get(http, "/api/revenue-scan/hospitals")["itens"]} == {HRVJ, HRC}


def test_entrada_invalida(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    cabecalho = {"Authorization": f"Bearer {token()}"}
    assert http.get("/api/revenue-scan/hospitals?natureza=QUALQUER", headers=cabecalho).status_code == 422
    assert http.get("/api/revenue-scan/panorama?cnes=12a", headers=cabecalho).status_code == 422
    assert http.get("/api/revenue-scan/hospitals?ordem=cor", headers=cabecalho).status_code == 422


def test_municipios_do_ibge(fabrica_sessao):
    corpo = [{"municipio-id": 2304400, "municipio-nome": "Fortaleza", "UF-sigla": "CE"},
             {"municipio-id": 3550308, "municipio-nome": "São Paulo", "UF-sigla": "SP"}]
    lista = municipios_ibge(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=corpo)))
    assert lista[1] == {"codigo": "3550308", "codigo_cnes": "355030", "nome": "São Paulo", "uf": "SP"}
    with fabrica_sessao() as db:
        assert carregar_municipios(db, lista) == 2
        assert carregar_municipios(db, lista) == 2
