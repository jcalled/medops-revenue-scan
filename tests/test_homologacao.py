"""
Homologação real pelo faturamento do hospital.

O que estes testes travam:
- o lote pega as AIH rejeitadas do processamento escolhido, com o que o FaturaSUS disse (quando pegou) ou o que o kit orienta;
- a planilha vai com a coluna de veredito vazia e volta preenchida: aplica pela AIH, vazio não apaga, AIH de fora é ignorada;
- confiabilidade como no núcleo: PARCIAL conta como erro, NAO_SEI fica fora, amostra mínima e piso de Wilson;
- só a administração da plataforma mexe.
"""
import io

import httpx

from app.domain.homologacao import _resumo, wilson_inferior
from app.models import SihPrevention
from app.seed.kits_motivo import aplicar
from tests.conftest import token
from tests.test_dados import ADMIN, _admin
from tests.test_kit import _dados
from tests.test_scan_api import HRC, HRVJ


def _preparar(fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        aplicar(db)
        db.add(SihPrevention(uf="CE", competencia="202606", cnes=HRVJ, n_aih="P1", grupo="PEGARIA", pegaria=True, motivos=[],
                             falhas=["SUS_CBO_PROC_INCOMP"], avisos=[],
                             mensagens=[{"code": "SUS_CBO_PROC_INCOMP", "status": "FAIL", "message": "CBO 225125 não executa o procedimento."}]))
        db.commit()


def test_lote_planilha_e_confiabilidade(app_com_nucleo, fabrica_sessao):
    _preparar(fabrica_sessao)
    http = _admin(app_com_nucleo)
    lote = http.post("/api/revenue-scan/homologation/batches", json={"cnes": [HRVJ, HRC], "competencia": "202606"}, headers=ADMIN)
    assert lote.status_code == 201, lote.text
    lote = lote.json()
    itens = {i["n_aih"]: i for i in lote["itens"]}
    assert set(itens) == {"P1", "H1", "V1", "G1", "O1", "Z1", "S1"}          # as rejeitadas de jun/26
    assert itens["P1"]["origem"] == "FATURASUS" and itens["P1"]["regras"] == ["SUS_CBO_PROC_INCOMP"]
    assert "CBO 225125" in itens["P1"]["o_que_diz"]
    assert itens["H1"]["origem"] == "KIT" and itens["H1"]["regras"] == ["KIT:060120"] and itens["H1"]["correcao"]
    assert lote["confiabilidade"]["respondidos"] == 0 and lote["confiabilidade"]["do_faturasus"] == 1

    planilha = http.get(f"/api/revenue-scan/homologation/batches/{lote['id']}/sheet", headers=ADMIN).content.decode("utf-8-sig")
    linhas = planilha.strip().splitlines()
    assert linhas[0].startswith("AIH;CNES;Hospital") and len(linhas) == 8
    # O faturamento preenche: P1 certo, H1 parcial, G1 "não sei", S1 vazio, e uma AIH que não é do lote.
    preenchida = "\n".join([linhas[0],
        "P1;;;;;;;;;certo;Confere com a escala;Ana (faturamento)",
        "H1;;;;;;;;;PARCIAL;Faltou o procedimento secundário;Ana (faturamento)",
        "G1;;;;;;;;;não sei;;Ana (faturamento)",
        "S1;;;;;;;;;;;",
        "9999999999999;;;;;;;;;CERTO;;"])
    r = http.post(f"/api/revenue-scan/homologation/batches/{lote['id']}/sheet", headers=ADMIN,
                  files={"arquivo": ("preenchida.csv", preenchida.encode("utf-8"), "text/csv")}).json()
    assert (r["aplicados"], r["sem_veredito"], r["ignorados"]) == (3, 1, ["9999999999999"])
    conf = r["lote"]["confiabilidade"]
    assert conf["respondidos"] == 3
    assert (conf["geral"]["julgados"], conf["geral"]["certos"], conf["geral"]["parciais"], conf["geral"]["nao_sei"]) == (2, 1, 1, 1)
    assert conf["faturasus"]["acerto"] == 1.0 and conf["faturasus"]["veredito"] == "SEM_DADOS"   # 1 parecer não diz nada
    assert conf["por_regra"][0]["chave"] == "SUS_CBO_PROC_INCOMP"

    # Pela tela: G1 vira ERRADO; o comentário antigo fica.
    g1 = next(i for i in r["lote"]["itens"] if i["n_aih"] == "G1")
    r = http.put(f"/api/revenue-scan/homologation/batches/{lote['id']}/items/{g1['id']}", headers=ADMIN,
                 json={"veredito": "ERRADO", "comentario": "Era faixa de AIH, resolvido pela secretaria"}).json()
    assert r["item"]["veredito"] == "ERRADO" and r["confiabilidade"]["geral"]["errados"] == 1


def test_metodo_de_confiabilidade():
    assert wilson_inferior(2, 2) < 0.4                    # 2 de 2 não é 100%
    assert wilson_inferior(60, 60) > 0.93
    assert _resumo("R", {"CERTO": 60})["veredito"] == "CONFIAVEL"
    assert _resumo("R", {"CERTO": 50, "PARCIAL": 10})["veredito"] == "EM_HOMOLOGACAO"
    assert _resumo("R", {"CERTO": 5, "NAO_SEI": 30})["veredito"] == "SEM_DADOS"      # não sei não entra na amostra


def test_so_administracao(app_com_nucleo, fabrica_sessao):
    _preparar(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json={"tenant_id": 1, "user_id": 10, "role": "admin", "platform_admin": False,
                                                                  "entitlement": {"product": "REVENUE_SCAN_SUS", "status": "ACTIVE", "modules": None, "scope": {}}}))
    assert http.post("/api/revenue-scan/homologation/batches", json={"cnes": [HRVJ]},
                     headers={"Authorization": f"Bearer {token()}"}).status_code == 403
