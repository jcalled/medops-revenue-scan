"""
Importação de OSS e hospitais por planilha.

O que estes testes travam:
- cria a OSS que falta pela sigla e vincula os hospitais pelo CNES;
- vínculo novo entra a confirmar, salvo quando a planilha diz confirmado;
- aceita ponto e vírgula ou vírgula, e reimportar não duplica;
- sem a coluna sigla, recusa dizendo as colunas aceitas; só a administração da plataforma importa.
"""
from app.models import ManagementOrganization, OrganizationEstablishment
from tests.conftest import token
from tests.test_dados import ADMIN, _admin

PLANILHA = "OSS;Nome;CNPJ;UF;CNES;Situação\nIGH;Instituto de Gestão;01.234.567/0001-89;GO;2338424;confirmado\nIGH;;;;2653982;\nISGH;ISGH;;CE;6848710;\n"


def test_importa_organizacoes_e_hospitais(app_com_nucleo, fabrica_sessao):
    http = _admin(app_com_nucleo)
    r = http.post("/api/revenue-scan/organizations/import", json={"conteudo": PLANILHA, "fonte": "planilha teste"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json() == {"organizacoes_criadas": ["IGH", "ISGH"], "vinculos_novos": 3, "erros": []}
    with fabrica_sessao() as db:
        igh = db.query(ManagementOrganization).filter_by(sigla="IGH").one()
        assert (igh.nome, igh.cnpj, igh.uf) == ("Instituto de Gestão", "01234567000189", "GO")
        situacoes = {u.cnes: u.situacao for u in db.query(OrganizationEstablishment).filter_by(organization_id=igh.id)}
        assert situacoes == {"2338424": "CONFIRMADO", "2653982": "A_CONFIRMAR"}

    # Vírgula também serve, e reimportar não duplica.
    r = http.post("/api/revenue-scan/organizations/import", json={"conteudo": PLANILHA.replace(";", ",").replace("01.234.567/0001-89", "")},
                  headers=ADMIN)
    assert r.json()["organizacoes_criadas"] == [] and r.json()["vinculos_novos"] == 0


def test_recusa_sem_sigla_e_sem_admin(app_com_nucleo):
    http = _admin(app_com_nucleo)
    r = http.post("/api/revenue-scan/organizations/import", json={"conteudo": "nome;cnes\nX;123\n"}, headers=ADMIN)
    assert r.status_code == 422 and "sigla" in r.json()["detail"]
    r = http.post("/api/revenue-scan/organizations/import", json={"conteudo": PLANILHA},
                  headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 403
