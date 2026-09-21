"""
Apresentação por link público.

O que estes testes travam:
- gerar é da administração; abrir é público, sem login, pelo token do link;
- o banco guarda só o hash do token; a lista não devolve o token;
- sem honorários, nenhum valor de honorário vai no retrato; com, vai o percentual;
- revogado ou vencido responde igual a inexistente; cada abertura conta.
"""
from datetime import datetime, timedelta, timezone

from app.models import PublicPresentation
from tests.conftest import token
from tests.test_dados import ADMIN, _admin
from tests.test_kit import _dados
from tests.test_scan_api import HRC, HRVJ


def _gerar(http, **extra):
    r = http.post("/api/revenue-scan/public-presentations", json={"cnes": [HRVJ, HRC], **extra}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def test_link_publico(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http = _admin(app_com_nucleo)
    assert http.post("/api/revenue-scan/public-presentations", json={"cnes": [HRVJ]},
                     headers={"Authorization": f"Bearer {token()}"}).status_code == 403

    link = _gerar(http)
    assert len(link["token"]) >= 40 and not link["com_honorarios"]
    with fabrica_sessao() as db:
        salvo = db.get(PublicPresentation, link["id"])
        assert salvo.token_hash != link["token"] and link["token"] not in str(salvo.retrato)

    aberto = http.get(f"/api/revenue-scan/public/presentations/{link['token']}")      # sem Authorization
    assert aberto.status_code == 200
    retrato = aberto.json()
    assert retrato["total"]["rejeitadas"]["valor"] == 462600.0 and retrato["percentual"] is None
    assert "honorarios" not in str(retrato)
    assert set(retrato["hospitais"][0]) == {"cnes", "nome", "total", "semelhantes", "prevenir", "cenarios"}

    lista = http.get("/api/revenue-scan/public-presentations", headers=ADMIN).json()["links"]
    assert lista[0]["visualizacoes"] == 1 and "token" not in lista[0]

    http.delete(f"/api/revenue-scan/public-presentations/{link['id']}", headers=ADMIN)
    assert http.get(f"/api/revenue-scan/public/presentations/{link['token']}").status_code == 404
    assert http.get("/api/revenue-scan/public/presentations/token-que-nao-existe").status_code == 404


def test_com_honorarios_e_vencimento(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http = _admin(app_com_nucleo)
    link = _gerar(http, mostrar_honorarios=True, percentual=20)
    retrato = http.get(f"/api/revenue-scan/public/presentations/{link['token']}").json()
    assert retrato["percentual"] == 20 and "honorarios" in retrato["cenarios"]["com_software_completo"]

    with fabrica_sessao() as db:
        db.get(PublicPresentation, link["id"]).expira_em = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert http.get(f"/api/revenue-scan/public/presentations/{link['token']}").status_code == 404
