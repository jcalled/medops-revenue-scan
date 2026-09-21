"""Prova pública: partição financeira, cronologia, limites e autorização."""
from datetime import date

import httpx
import pytest

from app.domain.kit import classificar
from app.domain.recuperacao import prazo_estimado, valor_cobrado
from app.engine.categorias import categorizar
from app.models import RecoveryItem, SihApprovedAih, SihRejection, SihRejectionReason
from tests.conftest import contrato, token
from tests.test_kit import _dados
from tests.test_scan_api import HRVJ, HRC


def test_janela_reapresentacao_e_motivo_de_prazo():
    # Exemplo do manual: alta em janeiro, reapresentação até junho — o 6º mês contando o da alta.
    assert prazo_estimado(date(2026, 1, 15)) == "202606"
    assert prazo_estimado(date(2026, 3, 31)) == "202608"
    assert prazo_estimado(date(2026, 9, 1)) == "202702"
    assert prazo_estimado(None) is None
    linha = {"situacao": "RECUPERAR", "categoria": "PROFISSIONAL", "dt_saida": "2026-03-31"}
    assert classificar(linha, "202607") == ("ALTA", "202608")
    assert classificar(linha, "202608") == ("ALTA", "202608")
    assert classificar(linha, "202609") == ("PRAZO_VENCIDO", "202608")
    # 040008: apresentada depois do 4º mês da alta, rejeição definitiva pela regra do MS.
    assert classificar({**linha, "categoria": "PRAZO"}, "202607") == ("PRAZO_VENCIDO", "202608")
    assert classificar({**linha, "dt_saida": None}, "202607") == ("INVESTIGAR", None)


def test_impedimento_nao_some_e_valor_ausente_nao_e_credito():
    assert categorizar(["060109", "010003"]).codigo == "ADMINISTRATIVO"
    assert categorizar(["060109", "999999"]).codigo == "OUTROS"
    assert categorizar(["060109", "040008"]).codigo == "PRAZO"
    assert valor_cobrado(RecoveryItem(valor_aprovado=None, valor_rejeitado=9000)) == 0
    assert valor_cobrado(RecoveryItem(valor_aprovado=0, valor_rejeitado=9000)) == 0
    assert valor_cobrado(RecoveryItem(valor_aprovado=800, valor_rejeitado=9000)) == 800


def test_dossie_particao_fontes_e_limites(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    url = f"/api/revenue-scan/hospitals/{HRVJ}/evidencias"
    response = http.get(url + "?referencia=202609", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 200, response.text
    d = response.json()
    particao = [d[k] for k in ("potencial_no_prazo", "aprovacao_localizada", "fora_janela", "nao_reapresentavel",
                                "a_validar")]
    assert sum(x["aih"] for x in particao) == d["rejeicao_documentada"]["aih"]
    assert sum(x["valor"] for x in particao) == pytest.approx(d["rejeicao_documentada"]["valor"])
    assert len(d["linhas"]) == len({l["n_aih"] for l in d["linhas"]})
    assert d["prevencao"] is None  # não inventa um teste ainda não executado
    assert d["cobertura"]["cargas_ausentes"] and d["cobertura"]["ultimo_rd"] is None
    assert d["prazo"]["meses"] == 5 and d["classe_dado"] == "PUBLICO"
    assert d["nao_reapresentavel"]["aih"] == 30  # as 29 de capacidade sem aprovação e a V1
    assert "falsos positivos" in " ".join(d["limites"])
    assert all("tratativa" not in l and not l["recebimento_comprovado"] for l in d["linhas"])
    assert http.get(url, headers={}).status_code == 401
    assert http.get(url + "?referencia=202613", headers={"Authorization": f"Bearer {token()}"}).status_code == 422


def test_dossie_nao_expande_escopo(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC]})))
    assert http.get(f"/api/revenue-scan/hospitals/{HRVJ}/evidencias",
                    headers={"Authorization": f"Bearer {token()}"}).status_code == 404


def test_aprovacao_anterior_nao_e_descrita_como_posterior(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        db.add(SihRejection(uf="CE", competencia="202607", cnes=HRVJ, n_aih="ANTERIOR", valor=1000,
                            dt_saida=date(2026, 4, 1)))
        db.add(SihRejectionReason(uf="CE", competencia="202607", cnes=HRVJ, n_aih="ANTERIOR", codigo_erro="060109"))
        db.add(SihApprovedAih(uf="CE", competencia="202606", cnes=HRVJ, n_aih="ANTERIOR", valor=1000))
        db.commit()
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    d = http.get(f"/api/revenue-scan/hospitals/{HRVJ}/evidencias",
                 headers={"Authorization": f"Bearer {token()}"}).json()
    linha = next(l for l in d["linhas"] if l["n_aih"] == "ANTERIOR")
    assert linha["classe"] == "JA_RECEBIDA" and not linha["aprovacao_posterior"]
    assert linha["competencias_aprovacao"] == ["202606"]
    assert not linha["recebimento_comprovado"]


def test_preserva_original_por_conteudo_e_confere_integridade(tmp_path, monkeypatch):
    from app.adapters.datasus import sha256
    from app.adapters.evidence_archive import caminho_preservado, preservar
    monkeypatch.setenv("EVIDENCE_ARCHIVE_DIR", str(tmp_path / "archive"))
    arquivo = tmp_path / "RJCE2607.dbc"
    arquivo.write_bytes(b"primeira publicacao")
    digest = sha256(arquivo)
    guardado = preservar(arquivo, digest)
    arquivo.write_bytes(b"republicacao")
    outro = preservar(arquivo, sha256(arquivo))
    assert guardado != outro and guardado.read_bytes() == b"primeira publicacao"
    assert preservar(arquivo, sha256(arquivo)) == outro
    with pytest.raises(ValueError):
        preservar(arquivo, digest + "0")
    assert caminho_preservado("../escape") is None


def test_historico_publico_mudancas_e_campos_ausentes(fabrica_sessao):
    from app.domain.historico_publico import historico_publico
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        aprovada = db.query(SihApprovedAih).filter_by(cnes=HRVJ).first()
        rejeitada = db.query(SihRejection).filter_by(cnes=HRVJ, n_aih=aprovada.n_aih).first()
        rejeitada.proc_realizado = "0301010001"
        aprovada.campos_publicos = {"procedimento": "9999999999", "dt_saida": rejeitada.dt_saida.isoformat()}
        aprovada.valor = float(rejeitada.valor) + 100
        db.commit()
        h = historico_publico(db, HRVJ, aprovada.n_aih, ["202605", "202606"])
        evento = h["eventos"][-1]
        assert evento["situacao"] == "APROVADA"
        assert {m["campo"] for m in evento["mudancas"]} == {"valor", "procedimento"}
        assert h["autoria_da_correcao"] == "NAO_DISPONIVEL_NOS_DADOS_PUBLICOS"
        aprovada.campos_publicos = None
        db.commit()
        h = historico_publico(db, HRVJ, aprovada.n_aih, ["202605", "202606"])
        assert [m["campo"] for m in h["eventos"][-1]["mudancas"]] == ["valor"]
        assert historico_publico(db, HRC, aprovada.n_aih, ["202605"]) is None


def test_historico_respeita_escopo_e_aih(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC]})))
    base = f"/api/revenue-scan/hospitals/{HRVJ}/aih/P1/historico-publico"
    assert http.get(base).status_code == 401
    assert http.get(base, headers={"Authorization": f"Bearer {token()}"}).status_code == 404
    assert http.get(f"/api/revenue-scan/hospitals/{HRC}/aih/inexistente/historico-publico",
                    headers={"Authorization": f"Bearer {token()}"}).status_code == 404


def test_historico_mes_igual_nao_inventa_ordem(fabrica_sessao):
    from app.domain.historico_publico import historico_publico
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        a = db.query(SihApprovedAih).filter_by(cnes=HRVJ).first()
        r = db.query(SihRejection).filter_by(cnes=HRVJ, n_aih=a.n_aih).first()
        a.competencia = r.competencia
        a.valor = float(r.valor) + 900
        db.commit()
        h = historico_publico(db, HRVJ, a.n_aih, [r.competencia])
        assert len(h["eventos"]) == 2
        assert h["alteracoes_observadas"] == 0


def test_carga_rd_preserva_campos_administrativos(fabrica_sessao, tmp_path):
    from tests.test_carga_sih import adapters, MAIO
    from app.jobs.carga_sih import carregar_competencia
    with fabrica_sessao() as db:
        carregar_competencia(db, "CE", "202605", adapters(MAIO["RD"], MAIO["RJ"], MAIO["ER"]), tmp_path)
        a = db.query(SihApprovedAih).filter_by(cnes=HRVJ).first()
        assert a.campos_publicos["procedimento"] == "0303060212"
        assert set(a.campos_publicos) == {"procedimento", "competencia_aih", "dt_internacao", "dt_saida"}
