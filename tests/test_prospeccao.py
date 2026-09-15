"""
CRM de prospecção e cadastro das organizações pela tela.

O que estes testes travam:
- a planilha entra pela aba com a coluna "Organização", mesmo com título em cima;
- reimportar atualiza o público e preserva etapa, contato e próxima ação;
- mudar etapa e proposta fica no histórico; nota entra no histórico;
- a organização nasce da prospecção com a sigla do nome, e as unidades citadas viram sugestão de CNES;
- vincular hospital confere a data; buscar hospital pede três letras;
- só administração da plataforma.
"""
from io import BytesIO

import httpx
import openpyxl

from app.models import Establishment
from tests.conftest import contrato, token

ADMIN = {"Authorization": f"Bearer {token(role='platform_admin', tenant_id=None)}"}
CABECALHO = ["Rank", "UF principal", "Organização", "Presença / Estados", "Situação / escopo 2026",
             "Hospitais confirmados", "Rede / porte confirmado", "Principais unidades / evidência", "Liderança pública",
             "Contato público", "Site oficial", "Fonte oficial / referência", "Score comercial", "Prioridade",
             "Fit principal", "Confiança dos dados", "Próxima ação comercial", "Status comercial", "Owner"]


def _planilha(linhas) -> bytes:
    livro = openpyxl.Workbook()
    livro.active.title = "Dashboard"
    livro.active.append(["PAINEL"])
    # Tabela curta no painel, também com "Organização": não é a lista completa.
    livro.active.append(["Rank", "Organização", "Score"])
    livro.active.append([1, linhas[0][2], 100])
    aba = livro.create_sheet("Top 50 OSS")
    aba.append(["TOP 50 – OSS"])
    aba.append(["Ranking comercial"])
    aba.append(CABECALHO)
    for linha in linhas:
        aba.append(linha)
    saida = BytesIO()
    livro.save(saida)
    return saida.getvalue()


SPDM = [1, "SP", "SPDM – Associação Paulista para o Desenvolvimento da Medicina", "SP + RJ", "Contratos atuais", 19,
        "19 hospitais", "Hospital São Paulo; Regional de Sorocaba; Pirajussara", "Ronaldo Laranjeira (presidente)",
        "Portal institucional", "https://spdm.org.br/", "https://spdm.org.br/contratos", 100, "A1",
        "GlosaAI SUS", "Alta", "Abordagem corporativa", "Não contatado", None]
EINSTEIN = [3, "SP", "Sociedade Beneficente Israelita Brasileira Albert Einstein", "SP + GO", "Rede pública", None,
            "30 unidades", "HUGO em Goiás", "A confirmar", "Portal Einstein", "https://www.einstein.br/", None, 97, "A1",
            "GlosaAI SUS", "Alta", "Gestão pública", "Não contatado", None]


def _http(app_com_nucleo, admin=True):
    corpo = contrato(platform_admin=True, tenant_id=None, role="platform_admin") if admin else contrato()
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=corpo))
    return http


def _importar(http, conteudo):
    return http.post("/api/revenue-scan/crm/prospects/import", headers=ADMIN,
                     files={"arquivo": ("oss.xlsx", conteudo, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_importa_planilha_e_preserva_andamento(app_com_nucleo):
    http = _http(app_com_nucleo)
    resposta = _importar(http, _planilha([SPDM, EINSTEIN]))
    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"criados": 2, "atualizados": 0}

    lista = http.get("/api/revenue-scan/crm/prospects", headers=ADMIN).json()
    assert lista["por_etapa"]["MAPEADA"] == 2 and [p["rank"] for p in lista["prospects"]] == [1, 3]
    spdm = lista["prospects"][0]
    assert spdm["sigla_sugerida"] == "SPDM" and spdm["hospitais_confirmados"] == 19 and spdm["uf"] == "SP"
    assert lista["prospects"][1]["sigla_sugerida"] is None

    mudado = http.patch(f"/api/revenue-scan/crm/prospects/{spdm['id']}", headers=ADMIN, json={
        "etapa": "REUNIAO", "contato_nome": "Diretoria de faturamento", "proxima_acao": "Levar o scan",
        "proxima_acao_em": "2026-09-30", "proposta_percentual": 15, "proposta_fixo": 6900,
    }).json()
    assert mudado["etapa_nome"] == "Reunião"
    assert [e["tipo"] for e in mudado["eventos"]][:2] == ["PROPOSTA", "ETAPA"]
    assert mudado["eventos"][1]["texto"] == "Mapeada → Reunião"

    SPDM_NOVA = SPDM.copy()
    SPDM_NOVA[5] = 20
    assert _importar(http, _planilha([SPDM_NOVA])).json() == {"criados": 0, "atualizados": 1}
    depois = http.get(f"/api/revenue-scan/crm/prospects/{spdm['id']}", headers=ADMIN).json()
    assert depois["hospitais_confirmados"] == 20 and depois["etapa"] == "REUNIAO"
    assert depois["proxima_acao"] == "Levar o scan" and depois["contato_nome"] == "Diretoria de faturamento"

    nota = http.post(f"/api/revenue-scan/crm/prospects/{spdm['id']}/notes", headers=ADMIN, json={"texto": "Ligar terça"})
    assert nota.status_code == 201 and nota.json()["eventos"][0]["texto"] == "Ligar terça"

    assert http.get("/api/revenue-scan/crm/prospects?etapa=REUNIAO", headers=ADMIN).json()["prospects"][0]["id"] == spdm["id"]
    assert http.patch(f"/api/revenue-scan/crm/prospects/{spdm['id']}", headers=ADMIN, json={"etapa": "SUMIU"}).status_code == 422
    assert _importar(http, b"nao e planilha").status_code == 422


def test_organizacao_nasce_da_prospeccao_com_sugestoes(app_com_nucleo, fabrica_sessao):
    with fabrica_sessao() as db:
        db.add_all([
            Establishment(cnes="2077485", nome_fantasia="HOSPITAL SAO PAULO", uf="SP"),
            Establishment(cnes="2079798", nome_fantasia="CONJUNTO HOSPITALAR DE SOROCABA", uf="SP"),
            Establishment(cnes="2080338", nome_fantasia="HOSPITAL REGIONAL DE SOROCABA", uf="SP"),
            Establishment(cnes="2078287", nome_fantasia="HOSPITAL GERAL DE PIRAJUSSARA", uf="SP"),
            Establishment(cnes="2337991", nome_fantasia="HOSPITAL REGIONAL DE SOROCABA", uf="MG"),
        ])
        db.commit()
    http = _http(app_com_nucleo)
    _importar(http, _planilha([SPDM]))
    spdm = http.get("/api/revenue-scan/crm/prospects", headers=ADMIN).json()["prospects"][0]

    criado = http.post(f"/api/revenue-scan/crm/prospects/{spdm['id']}/organization", headers=ADMIN, json={})
    assert criado.status_code == 201
    org = criado.json()["organizacao"]
    assert org["sigla"] == "SPDM" and org["hospitais"] == 0
    assert http.post(f"/api/revenue-scan/crm/prospects/{spdm['id']}/organization", headers=ADMIN, json={}).status_code == 409

    http.put(f"/api/revenue-scan/organizations/{org['id']}/units/2080338", headers=ADMIN,
             json={"sigla": "HRS", "situacao": "CONFIRMADO", "fonte": "spdm.org.br/contratos"})
    sugestoes = http.get(f"/api/revenue-scan/crm/prospects/{spdm['id']}/suggestions", headers=ADMIN).json()
    assert sugestoes["ufs"] == ["SP", "RJ"]
    por_citado = {s["citado"]: s["candidatos"] for s in sugestoes["sugestoes"]}
    assert [c["cnes"] for c in por_citado["Regional de Sorocaba"]] == ["2080338", "2079798"]
    assert por_citado["Regional de Sorocaba"][0]["vinculado"] is True
    assert [c["cnes"] for c in por_citado["Pirajussara"]] == ["2078287"]


def test_cadastro_de_organizacao_e_unidades(app_com_nucleo, fabrica_sessao):
    with fabrica_sessao() as db:
        db.add(Establishment(cnes="2338424", nome_fantasia="HOSPITAL ESTADUAL DE URGENCIAS DE GOIAS", uf="GO"))
        db.commit()
    http = _http(app_com_nucleo)
    criada = http.post("/api/revenue-scan/organizations", headers=ADMIN,
                       json={"sigla": "agir", "nome": "Associação de Gestão, Inovação e Resultados em Saúde", "uf": "go",
                             "cnpj": "05.029.600/0001-04"})
    assert criada.status_code == 201, criada.text
    org = criada.json()
    assert (org["sigla"], org["uf"], org["cnpj"]) == ("AGIR", "GO", "05029600000104")
    assert http.post("/api/revenue-scan/organizations", headers=ADMIN, json={"sigla": "AGIR", "nome": "outra"}).status_code == 409

    busca = http.get("/api/revenue-scan/establishments?q=urgencias&uf=GO", headers=ADMIN).json()
    assert [e["cnes"] for e in busca["estabelecimentos"]] == ["2338424"]
    assert http.get("/api/revenue-scan/establishments?q=2338424", headers=ADMIN).json()["estabelecimentos"][0]["uf"] == "GO"
    assert http.get("/api/revenue-scan/establishments?q=ur", headers=ADMIN).status_code == 422

    vinculada = http.put(f"/api/revenue-scan/organizations/{org['id']}/units/2338424", headers=ADMIN,
                         json={"sigla": "HUGOL", "situacao": "CONFIRMADO", "fonte": "agirsaude.org.br"}).json()
    unidade = vinculada["unidades"][0]
    assert unidade["nome"] == "HOSPITAL ESTADUAL DE URGENCIAS DE GOIAS" and unidade["situacao"] == "CONFIRMADO"
    assert unidade["verificado_em"] is not None
    assert http.put(f"/api/revenue-scan/organizations/{org['id']}/units/2338424", headers=ADMIN,
                    json={"situacao": "TALVEZ"}).status_code == 422

    assert http.delete(f"/api/revenue-scan/organizations/{org['id']}/units/2338424", headers=ADMIN).json()["unidades"] == []
    assert http.delete(f"/api/revenue-scan/organizations/{org['id']}/units/2338424", headers=ADMIN).status_code == 404


def test_so_administracao(app_com_nucleo):
    http = _http(app_com_nucleo, admin=False)
    tenant = {"Authorization": f"Bearer {token()}"}
    assert http.get("/api/revenue-scan/crm/prospects", headers=tenant).status_code == 403
    assert http.post("/api/revenue-scan/organizations", headers=tenant, json={"sigla": "X", "nome": "Y"}).status_code == 403
    assert http.get("/api/revenue-scan/establishments?q=hospital", headers=tenant).status_code == 403
