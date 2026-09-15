"""
FaturaSUS por motivo.

O que estes testes travam:
- a taxa medida de cada motivo sai da prevenção gravada, refazer substitui e as UFs somam;
- o "passou no FaturaSUS" é registrado com a situação e validado;
- a reapresentação só vale num processamento depois da rejeição;
- o resultado no SUS: aprovada, rejeitada de novo (com os motivos novos) ou aguardando os dados do mês;
- por motivo da rejeição trabalhada, quanto o "passou" acertou — no catálogo e no kit de recuperação.
"""
from datetime import date

import httpx

from app.domain.kits_motivo import atualizar_estatisticas_prevencao, estatisticas_prevencao
from app.models import SihPrevention, SihRejection, SihRejectionReason
from tests.conftest import contrato
from tests.test_kit import _dados, _get
from tests.test_kits_motivo import TENANT, _kit
from tests.test_scan_api import HRVJ


def _prevencao(db, uf, n_aih, motivos):
    db.add(SihPrevention(uf=uf, competencia="202606", cnes=HRVJ, n_aih=n_aih, grupo="SEM_REGRA", pegaria=False,
                         motivos=motivos, falhas=[], avisos=[], mensagens=[], referencias={}))


def test_taxa_medida_por_motivo(fabrica_sessao):
    with fabrica_sessao() as db:
        _prevencao(db, "CE", "A1", [{"codigo": "060082", "grupo": "CONFERIVEL", "regras": ["SUS_DIARIAS_CAPACIDADE"],
                                     "pegaria": True}])
        _prevencao(db, "CE", "A2", [{"codigo": "060082", "grupo": "CONFERIVEL", "regras": [], "pegaria": False},
                                    {"codigo": "010003", "grupo": "BLOQUEIO_DO_GESTOR", "regras": [], "pegaria": False}])
        _prevencao(db, "PE", "A3", [{"codigo": "060082", "grupo": "CONFERIVEL", "regras": [], "pegaria": True}])
        db.commit()
        assert atualizar_estatisticas_prevencao(db, "CE") == 2
        assert atualizar_estatisticas_prevencao(db, "PE") == 1
        assert atualizar_estatisticas_prevencao(db, "CE") == 2

        ce = estatisticas_prevencao(db, ["CE"])
        assert ce["060082"] == {
            "grupo": "CONFERIVEL", "grupo_nome": "O FaturaSUS confere com o dado público",
            "leitura": "O 'passou' vale na medida da taxa: quanto maior, mais confiável.",
            "avaliadas": 2, "pegaria": 1, "taxa": 0.5, "regras_que_pegaram": ["SUS_DIARIAS_CAPACIDADE"],
        }
        assert ce["010003"]["taxa"] == 0.0 and "não diz nada" in ce["010003"]["leitura"]
        todas = estatisticas_prevencao(db)
        assert (todas["060082"]["avaliadas"], todas["060082"]["taxa"]) == (3, 0.6667)


def test_resultado_da_reapresentacao_e_confiabilidade(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        # A ...001, rejeitada por capacidade em maio, volta rejeitada em julho por UTI.
        db.add(SihRejection(uf="CE", competencia="202607", cnes=HRVJ, n_aih="2326000000001", valor=15000.0,
                            dt_saida=date(2026, 6, 10)))
        db.add(SihRejectionReason(uf="CE", competencia="202607", cnes=HRVJ, n_aih="2326000000001", codigo_erro="060084"))
        db.add(_kit("060082", "INCERTA"))
        db.add(_kit("060109", "ALTA"))
        _prevencao(db, "CE", "2326000000002", [{"codigo": "060082", "grupo": "CONFERIVEL", "regras": [], "pegaria": True}])
        db.commit()
        atualizar_estatisticas_prevencao(db, "CE")
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))

    def marcar(n_aih, **corpo):
        return http.put(f"/api/revenue-scan/aih/{n_aih}/treatment", json={"situacao": "REAPRESENTADA", **corpo},
                        headers=TENANT)

    assert marcar("P1", competencia_reapresentacao="202606").status_code == 422
    assert marcar("P1", competencia_reapresentacao="202609", faturasus="TALVEZ").status_code == 422

    aprovada = marcar("2326000000000", competencia_reapresentacao="202606", faturasus="PASSOU").json()
    assert aprovada["tratativa"]["resultado"] == {
        "situacao": "APROVADA", "nome": "Aprovada pelo SUS", "competencia": "202606", "valor": 0.0, "motivos": [],
        "motivos_originais": ["060082"],
    }
    assert aprovada["tratativa"]["faturasus_nome"] == "Passou no FaturaSUS"

    de_novo = marcar("2326000000001", competencia_reapresentacao="202607", faturasus="PASSOU").json()["tratativa"]
    assert de_novo["resultado"]["situacao"] == "REJEITADA_DE_NOVO"
    assert (de_novo["resultado"]["competencia"], de_novo["resultado"]["motivos"]) == ("202607", ["060084"])

    aguardando = marcar("P1", competencia_reapresentacao="202609", faturasus="NAO_PASSOU").json()
    assert aguardando["tratativa"]["resultado"]["situacao"] == "AGUARDANDO_DADOS"
    assert aguardando["historico"][0]["faturasus"] == "NAO_PASSOU"

    kits = {k["codigo"]: k for k in _get(http, "/api/revenue-scan/motive-kits?uf=CE")["kits"]}
    assert kits["060082"]["confiabilidade"] == {
        "passou": 2, "passou_aprovadas": 1, "passou_rejeitadas": 1,
        "nao_passou": 0, "nao_passou_aprovadas": 0, "nao_passou_rejeitadas": 0, "aguardando": 0,
        "decididas": 2, "taxa_acerto": 0.5, "amostra_pequena": True,
    }
    assert kits["060082"]["faturasus"]["taxa"] == 1.0
    assert (kits["060109"]["confiabilidade"]["nao_passou"], kits["060109"]["confiabilidade"]["aguardando"]) == (1, 1)
    assert kits["060109"]["confiabilidade"]["taxa_acerto"] is None and kits["060109"]["faturasus"] is None

    kit = _get(http, "/api/revenue-scan/kit?uf=CE&referencia=202609&lista=todas")
    item = next(i for i in kit["itens"] if i["n_aih"] == "2326000000001")
    assert item["tratativa"]["resultado"]["situacao"] == "REJEITADA_DE_NOVO" and item["tratativa"]["faturasus"] == "PASSOU"
    assert kit["kits_motivo"]["060082"]["confiabilidade"]["taxa_acerto"] == 0.5
