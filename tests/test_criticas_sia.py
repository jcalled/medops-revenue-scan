"""
Leitura do relatório de críticas do SIA.

O que estes testes travam:
- planilha ou CSV com cabeçalho: usa as colunas (APAC, procedimento, erro, valor, competência);
- relatório em texto: acha APAC, procedimento, valor e o erro na linha;
- Excel também; o grupo sai da descrição do erro, com teto separado do que volta por correção;
- pela Área do contrato, a leitura fica no arquivo e a lista não carrega as APAC uma a uma.
"""
import io

import httpx
from openpyxl import Workbook

from app.domain import criticas_sia
from tests.conftest import contrato, token

CSV = (
    "Competência;Nº da APAC;Procedimento;Código do erro;Descrição do erro;Valor apresentado\n"
    "202607;2326202997613;0304050024;101;CBO do profissional incompatível com o procedimento;1.234,56\n"
    "202607;2326202997614;0304040045;205;Ultrapassou o teto financeiro da programação;2.000,00\n"
    "202607;2326202997615;0304040045;310;CID incompatível com o procedimento principal;500,00\n"
    "202607;2326202997616;0304040045;999;Crítica desconhecida;10,00\n"
)
TEXTO = (
    "SECRETARIA DA SAÚDE - RELATÓRIO DE CRÍTICAS DO SIA - COMPETÊNCIA 07/2026\n"
    "APAC           PROCEDIMENTO  VALOR      ERRO\n"
    "2326202997613  0304050024    1.234,56   CNS DO PACIENTE INVÁLIDO\n"
    "2326202997700  0304040045    800,00     APAC COM VALIDADE VENCIDA\n"
    "Total de APAC criticadas: 2\n"
)


def test_planilha_com_cabecalho():
    r = criticas_sia.ler(CSV.encode("utf-8"), "criticas.csv")
    assert (r["formato"], r["linhas_lidas"], r["apac"]) == ("PLANILHA", 4, 4)
    grupos = {g["grupo"]: g for g in r["grupos"]}
    assert grupos["TETO"]["valor"] == 2000.0 and not grupos["TETO"]["corrigivel"]
    assert grupos["PROFISSIONAL"]["valor"] == 1234.56 and grupos["COMPATIBILIDADE"]["valor"] == 500.0
    assert grupos["OUTROS"]["linhas"] == 1
    assert r["corrigivel"] == {"apac": 2, "valor": 1734.56}
    assert r["itens"][0]["competencia"] == "202607" and r["itens"][0]["erro"].startswith("101 ")


def test_relatorio_em_texto():
    r = criticas_sia.ler(TEXTO.encode("latin-1"), "criticas.txt")
    assert (r["formato"], r["linhas_lidas"]) == ("TEXTO", 2)
    [paciente, autorizacao] = sorted(r["itens"], key=lambda i: i["apac"])
    assert (paciente["procedimento"], paciente["valor"], paciente["grupo"]) == ("0304050024", 1234.56, "PACIENTE")
    assert autorizacao["grupo"] == "AUTORIZACAO" and "VALIDADE VENCIDA" in autorizacao["erro"]


def test_excel():
    livro = Workbook()
    aba = livro.active
    for linha in [l.split(";") for l in CSV.strip().splitlines()]:
        aba.append(linha)
    saida = io.BytesIO()
    livro.save(saida)
    r = criticas_sia.ler(saida.getvalue(), "criticas.xlsx")
    assert r["linhas_lidas"] == 4 and r["valor_total"] == 3744.56


def test_pela_area_do_contrato(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    cabecalho = {"Authorization": f"Bearer {token()}"}
    r = http.post("/api/revenue-scan/contract/files", headers=cabecalho, data={"tipo": "CRITICAS_SIA"},
                  files={"arquivo": ("criticas.csv", CSV.encode("utf-8"), "text/csv")})
    assert r.status_code == 201, r.text
    assert "itens" not in r.json()["resumo"] and r.json()["resumo"]["corrigivel"]["apac"] == 2
    detalhe = http.get(f"/api/revenue-scan/contract/files/{r.json()['id']}", headers=cabecalho).json()
    assert len(detalhe["resumo"]["itens"]) == 4
    vazio = http.post("/api/revenue-scan/contract/files", headers=cabecalho, data={"tipo": "CRITICAS_SIA"},
                      files={"arquivo": ("vazio.csv", b"a;b;c\n1;2;3\n", "text/csv")})
    assert vazio.status_code == 422 and "APAC" in vazio.json()["detail"]
