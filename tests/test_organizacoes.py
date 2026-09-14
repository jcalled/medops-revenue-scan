"""
Organizações e o resumo por OSS, dentro do escopo do contrato.

O que estes testes travam:
- o cadastro do ISGH pode rodar de novo sem duplicar;
- tenant com escopo de CNES só vê esses hospitais da organização;
- organização fora do escopo responde 404, como se não existisse;
- `period_months` limita os meses do resumo;
- hospital da organização sem AIH no período aparece como "sem dado".
"""
import httpx
import pytest

from app.models import Establishment, ManagementOrganization, SihHospitalMonth
from app.seed.organizacoes import aplicar, buscar_estabelecimentos, ler_csv
from tests.conftest import contrato, token

HRVJ, HRC = "9672427", "6779522"


def _dados(fabrica_sessao) -> int:
    with fabrica_sessao() as db:
        aplicar(db)
        aplicar(db)
        db.add_all([
            Establishment(cnes=HRVJ, nome_fantasia="HOSPITAL REGIONAL VALE DO JAGUARIBE", uf="CE"),
            SihHospitalMonth(uf="CE", cnes=HRVJ, competencia="202606", aih_aprovadas=800, valor_aprovado=2_000_000,
                             aih_rejeitadas=200, valor_rejeitado=1_000_000),
            SihHospitalMonth(uf="CE", cnes=HRVJ, competencia="202607", aih_aprovadas=900, valor_aprovado=2_500_000,
                             aih_rejeitadas=100, valor_rejeitado=500_000),
            SihHospitalMonth(uf="CE", cnes=HRC, competencia="202607", aih_aprovadas=700, valor_aprovado=1_500_000,
                             aih_rejeitadas=50, valor_rejeitado=100_000),
        ])
        db.commit()
        org = db.query(ManagementOrganization).one()
        assert len(org.unidades) == 8
        return org.id


def _get(http, caminho):
    return http.get(caminho, headers={"Authorization": f"Bearer {token()}"})


def test_tenant_ve_so_os_hospitais_do_escopo(app_com_nucleo, fabrica_sessao):
    org_id = _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRVJ]})))
    assert _get(http, "/api/revenue-scan/organizations").json()["organizations"] == [
        {"id": org_id, "sigla": "ISGH", "nome": "Instituto de Saúde e Gestão Hospitalar", "uf": "CE", "hospitais": 1}
    ]
    corpo = _get(http, f"/api/revenue-scan/organizations/{org_id}/summary").json()
    assert [h["cnes"] for h in corpo["hospitais"]] == [HRVJ]
    assert corpo["hospitais"][0]["sigla"] == "HRVJ"
    assert corpo["hospitais"][0]["nome"] == "HOSPITAL REGIONAL VALE DO JAGUARIBE"
    assert corpo["total"]["aih_rejeitadas"] == 300 and corpo["total"]["taxa_valor"] == 0.25
    assert corpo["classe_dado"] == "PUBLICO" and "estimativa" in corpo["ressalva"]


def test_organizacao_fora_do_escopo_e_404(app_com_nucleo, fabrica_sessao):
    org_id = _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"organizations": ["FGH"]})))
    assert _get(http, "/api/revenue-scan/organizations").json()["organizations"] == []
    assert _get(http, f"/api/revenue-scan/organizations/{org_id}/summary").status_code == 404
    assert _get(http, "/api/revenue-scan/organizations/9999/summary").status_code == 404


def test_periodo_do_contrato_limita_os_meses(app_com_nucleo, fabrica_sessao):
    org_id = _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"period_months": 1})))
    corpo = _get(http, f"/api/revenue-scan/organizations/{org_id}/summary").json()
    assert corpo["competencias"] == ["202607"]
    assert corpo["total"]["aih_rejeitadas"] == 150
    # Os outros seis hospitais do ISGH não têm AIH carregada: sem dado, não zero.
    assert HRVJ not in corpo["sem_producao_no_periodo"] and len(corpo["sem_producao_no_periodo"]) == 6


def test_qualquer_oss_entra_por_planilha(fabrica_sessao, tmp_path):
    planilha = tmp_path / "oss.csv"
    planilha.write_text(
        "sigla;nome;cnpj;uf;site;fonte;cnes;sigla_unidade;situacao;verificado_em\n"
        "fgh;Fundação Gestão Hospitalar Martiniano Fernandes;09.039.744/0001-94;PE;https://fghsaude.org.br/;"
        "https://fghsaude.org.br/home/institucional;1234567;HEC;CONFIRMADO;2026-09-20\n"
        "FGH;Fundação Gestão Hospitalar Martiniano Fernandes;;PE;;;765432;;;\n"
        "AGIR;Associação de Gestão, Inovação e Resultados em Saúde;;GO;;;;;;\n",
        encoding="utf-8",
    )
    with fabrica_sessao() as db:
        assert aplicar(db, ler_csv(planilha)) == {"organizacoes": 2, "unidades": 2}
        fgh = db.query(ManagementOrganization).filter_by(sigla="FGH").one()
        assert fgh.cnpj == "09039744000194" and fgh.uf == "PE"
        assert {(u.cnes, u.sigla, u.situacao) for u in fgh.unidades} == {
            ("1234567", "HEC", "CONFIRMADO"), ("0765432", None, "A_CONFIRMAR")}
        assert db.query(ManagementOrganization).filter_by(sigla="AGIR").one().unidades == []


def test_planilha_com_cnes_invalido_e_recusada(tmp_path):
    planilha = tmp_path / "oss.csv"
    planilha.write_text("sigla,nome,cnes\nX,Organização X,123456789\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Linha 2: CNES inválido"):
        ler_csv(planilha)


def test_busca_de_cnes_pelo_nome(fabrica_sessao):
    with fabrica_sessao() as db:
        db.add_all([
            Establishment(cnes="2338424", nome_fantasia="HOSPITAL ESTADUAL DE URGENCIAS DE GOIAS HUGO", uf="GO"),
            Establishment(cnes="6779522", nome_fantasia="HOSPITAL REGIONAL DO CARIRI", uf="CE"),
            Establishment(cnes="9672427", nome_fantasia="HOSPITAL REGIONAL VALE DO JAGUARIBE", uf="CE"),
        ])
        db.commit()
        assert [e["cnes"] for e in buscar_estabelecimentos(db, "regional")] == ["6779522", "9672427"]
        assert buscar_estabelecimentos(db, "regional", uf="GO") == []
        assert buscar_estabelecimentos(db, "hugo", uf="go")[0]["cnes"] == "2338424"


def test_competencia_invalida_e_422(app_com_nucleo, fabrica_sessao):
    org_id = _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    assert _get(http, f"/api/revenue-scan/organizations/{org_id}/summary?competencias=2026-07").status_code == 422
    corpo = _get(http, f"/api/revenue-scan/organizations/{org_id}/summary?competencias=202606").json()
    assert corpo["competencias"] == ["202606"] and corpo["total"]["aih_rejeitadas"] == 200
