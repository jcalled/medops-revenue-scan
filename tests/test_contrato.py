"""
Área do contrato.

O que estes testes travam:
- a lista do que pedir diz, para cada arquivo, para que serve, como obter e se o sistema já lê;
- o arquivo fica do tenant: outro tenant não lista, não baixa e não apaga (responde como inexistente);
- a administração da plataforma não entra — dado de hospital é do cliente;
- o SISAIH01 só entra com a análise do FaturaSUS, e o conteúdo fica no núcleo, não aqui;
- tipo, formato e tamanho são conferidos.
"""
import json

import httpx

from tests.conftest import contrato, token
from tests.test_dados import ADMIN, _admin

CLIENTE = {"Authorization": f"Bearer {token()}"}
OUTRO = {"Authorization": f"Bearer {token(tenant_id=2, sub='20')}"}


def _http(app_com_nucleo):
    def responder(r):
        tenant = 2 if r.headers.get("Authorization") == OUTRO["Authorization"] else 1
        return httpx.Response(200, json=contrato(tenant_id=tenant, user_id=20 if tenant == 2 else 10))
    http, _ = app_com_nucleo(responder)
    return http


def test_lista_do_que_pedir(app_com_nucleo):
    http = _http(app_com_nucleo)
    itens = {i["tipo"]: i for i in http.get("/api/revenue-scan/contract/checklist", headers=CLIENTE).json()["itens"]}
    assert itens["SISAIH01"]["leitura"] == "FATURASUS" and itens["SISAIH01"]["obrigatorio"]
    assert itens["PROFISSIONAIS"]["leitura"] == "GUARDADO" and "guardado" in itens["PROFISSIONAIS"]["leitura_nome"].lower()
    assert all(i["como_obter"] and i["para_que"] for i in itens.values())


def test_arquivo_fica_do_tenant(app_com_nucleo):
    http = _http(app_com_nucleo)
    r = http.post("/api/revenue-scan/contract/files", headers=CLIENTE, data={"tipo": "PROFISSIONAIS", "competencia": "202607"},
                  files={"arquivo": ("escala.csv", b"nome;cbo\nFulano;225125\n", "text/csv")})
    assert r.status_code == 201, r.text
    arquivo = r.json()
    assert arquivo["tipo"] == "PROFISSIONAIS" and len(arquivo["sha256"]) == 64

    assert [a["id"] for a in http.get("/api/revenue-scan/contract/files", headers=CLIENTE).json()["arquivos"]] == [arquivo["id"]]
    assert http.get(f"/api/revenue-scan/contract/files/{arquivo['id']}/download", headers=CLIENTE).content.startswith(b"nome;cbo")
    # Outro tenant: não vê, não baixa, não apaga.
    assert http.get("/api/revenue-scan/contract/files", headers=OUTRO).json()["arquivos"] == []
    assert http.get(f"/api/revenue-scan/contract/files/{arquivo['id']}/download", headers=OUTRO).status_code == 404
    assert http.delete(f"/api/revenue-scan/contract/files/{arquivo['id']}", headers=OUTRO).status_code == 404
    assert http.delete(f"/api/revenue-scan/contract/files/{arquivo['id']}", headers=CLIENTE).status_code == 204


def test_administracao_da_plataforma_nao_entra(app_com_nucleo):
    assert _admin(app_com_nucleo).get("/api/revenue-scan/contract/checklist", headers=ADMIN).status_code == 403


def test_sisaih01_so_com_analise_e_sem_conteudo(app_com_nucleo):
    http = _http(app_com_nucleo)
    sem = http.post("/api/revenue-scan/contract/files", headers=CLIENTE, data={"tipo": "SISAIH01"},
                    files={"arquivo": ("aih.txt", b"x" * 20, "text/plain")})
    assert sem.status_code == 422
    com = http.post("/api/revenue-scan/contract/files", headers=CLIENTE,
                    data={"tipo": "SISAIH01", "analysis_id": "77", "resumo": json.dumps({"aihs": 10, "fail": 3})},
                    files={"arquivo": ("aih.txt", b"x" * 20, "text/plain")}).json()
    assert com["analysis_id"] == 77 and com["resumo"] == {"aihs": 10, "fail": 3}
    assert http.get(f"/api/revenue-scan/contract/files/{com['id']}/download", headers=CLIENTE).status_code == 409


def test_tipo_e_formato(app_com_nucleo):
    http = _http(app_com_nucleo)
    assert http.post("/api/revenue-scan/contract/files", headers=CLIENTE, data={"tipo": "XYZ"},
                     files={"arquivo": ("a.csv", b"1", "text/csv")}).status_code == 422
    assert http.post("/api/revenue-scan/contract/files", headers=CLIENTE, data={"tipo": "PROFISSIONAIS"},
                     files={"arquivo": ("a.exe", b"1", "application/octet-stream")}).status_code == 422
