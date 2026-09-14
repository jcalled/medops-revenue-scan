"""
Recalcular o scan a partir do banco e ler pelas rotas.

O que estes testes travam:
- o recálculo monta perfis do banco (produção, procedimentos, leitos,
  habilitações, perdas por motivo) e grava semelhantes, indicadores,
  oportunidades e score;
- recalcular o mesmo período substitui, não duplica;
- o scan do hospital e o ranking da OSS respeitam o escopo do contrato.
"""
import httpx
from sqlalchemy import func, select

from app.jobs.recalcular import recalcular
from app.models import (
    CnesBed, CnesEnablement, Establishment, HospitalScore, ManagementOrganization, Opportunity, PeerGroup,
    SihApprovedAih, SihHospitalMonth, SihHospitalProcedureMonth, SihRejection, SihRejectionReason,
)
from app.seed.organizacoes import aplicar
from tests.conftest import contrato, token

HRVJ, HRC = "9672427", "6779522"
PARES = [f"23000{i:02d}" for i in range(7)]
MESES = ["202605", "202606", "202607"]
PROC = "0303010010"


def popular(db):
    aplicar(db)
    for cnes in [HRVJ, HRC, *PARES]:
        db.add(Establishment(cnes=cnes, nome_fantasia=f"HOSPITAL {cnes}", uf="CE", natureza_juridica="1023"))
        db.add(CnesBed(uf="CE", competencia="202607", cnes=cnes, codigo_leito="33", tipo_leito="2",
                       qt_existente=100, qt_sus=100))
        for competencia in MESES:
            valor = 500_000.0 if cnes == HRVJ else 650_000.0
            rejeitado = 150_000.0 if cnes == HRVJ else 5_000.0
            db.add(SihHospitalMonth(uf="CE", cnes=cnes, competencia=competencia, aih_aprovadas=300,
                                    valor_aprovado=valor, aih_rejeitadas=30, valor_rejeitado=rejeitado,
                                    diarias=1500, permanencia_dias=1500))
            db.add(SihHospitalProcedureMonth(uf="CE", cnes=cnes, competencia=competencia, proc_realizado=PROC,
                                             complexidade="02", aih=300, valor=valor, diarias=1500,
                                             permanencia_dias=1500))
    db.add(CnesEnablement(uf="CE", competencia="202607", cnes=HRVJ, habilitacao="2601", competencia_inicio="201501"))
    # 30 AIH do HRVJ rejeitadas por capacidade em maio, nunca aprovadas.
    for i in range(30):
        n_aih = f"23260000000{i:02d}"
        db.add(SihRejection(uf="CE", competencia="202605", cnes=HRVJ, n_aih=n_aih, valor=15_000.0))
        db.add(SihRejectionReason(uf="CE", competencia="202605", cnes=HRVJ, n_aih=n_aih, codigo_erro="060082"))
    # Uma que voltou aprovada não é perda.
    db.add(SihApprovedAih(uf="CE", competencia="202606", cnes=HRVJ, n_aih="2326000000000"))
    db.commit()


def test_recalcular_grava_e_substitui(fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)
        primeiro = recalcular(db, ufs=["CE"])
        segundo = recalcular(db, ufs=["CE"])
        assert primeiro["hospitais"] == segundo["hospitais"] == 9 and primeiro["competencias"] == MESES
        assert db.scalar(select(func.count()).select_from(HospitalScore)) == 9
        assert db.scalar(select(func.count()).select_from(PeerGroup)) == 9
        oportunidades = db.execute(select(Opportunity).where(Opportunity.cnes == HRVJ)).scalars().all()
        capacidade = next(o for o in oportunidades if o.categoria == "CAPACIDADE")
        assert capacidade.evidence["aih_nao_recuperadas"] == 29
        assert float(capacidade.observed_value) == 435_000.0
        assert db.scalar(select(func.count()).select_from(Opportunity)) == segundo["oportunidades"]


def _get(http, caminho):
    return http.get(caminho, headers={"Authorization": f"Bearer {token()}"})


def test_scan_do_hospital(app_com_nucleo, fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)
        recalcular(db, ufs=["CE"])
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    corpo = _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/scan").json()
    assert corpo["hospital"]["nome"] == f"HOSPITAL {HRVJ}" and corpo["hospital"]["porte"] == "51 a 150 leitos"
    assert corpo["hospital"]["organizacoes"] == [{"sigla": "ISGH", "sigla_unidade": "HRVJ"}]
    assert corpo["semelhantes"]["criterio"].startswith("mesma UF") and len(corpo["semelhantes"]["hospitais"]) == 8
    assert corpo["score"] > 0 and corpo["principal_problema"] == "Diárias acima da capacidade instalada"
    rejeicao = next(i for i in corpo["indicadores"] if i["metrica"] == "taxa_rejeicao_valor")
    assert rejeicao["valor"] == 0.2308 and rejeicao["percentil"] == 100.0
    assert corpo["oportunidades"][0]["titulo"] and corpo["classe_dado"] == "PUBLICO"


def test_scan_fora_do_escopo_e_404(app_com_nucleo, fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)
        recalcular(db, ufs=["CE"])
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC]})))
    assert _get(http, f"/api/revenue-scan/hospitals/{HRVJ}/scan").status_code == 404
    assert _get(http, f"/api/revenue-scan/hospitals/{HRC}/scan").status_code == 200
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"organizations": ["ISGH"]})))
    assert _get(http, f"/api/revenue-scan/hospitals/{PARES[0]}/scan").status_code == 404
    assert _get(http, "/api/revenue-scan/hospitals/abc/scan").status_code == 404


def test_ranking_da_organizacao(app_com_nucleo, fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)
        recalcular(db, ufs=["CE"])
        org_id = db.execute(select(ManagementOrganization.id).where(ManagementOrganization.sigla == "ISGH")).scalar()
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    corpo = _get(http, f"/api/revenue-scan/organizations/{org_id}/opportunities").json()
    assert corpo["hospitais_da_organizacao"] == 8 and corpo["hospitais_analisados"] == 2
    assert [h["sigla"] for h in corpo["ranking"]] == ["HRVJ", "HRC"]
    hrvj = corpo["ranking"][0]
    assert hrvj["impacto_confirmado"] > 0
    assert hrvj["impacto_confirmado"] + hrvj["impacto_sinais"] == hrvj["impacto_estimado"]
    assert corpo["impacto_confirmado_total"] + corpo["impacto_sinais_total"] == corpo["impacto_estimado_total"]
    assert corpo["rejeicao"]["perda_liquida_aih"] == 29 and len(corpo["sem_scan"]) == 6
