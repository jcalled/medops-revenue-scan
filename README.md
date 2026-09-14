# MedOps Revenue Scan SUS

Diagnóstico de receita SUS de hospitais e Organizações Sociais de Saúde a partir
dos dados públicos do DATASUS: receita rejeitada comprovada pelo SUS,
oportunidade financeira estimada, comparação com hospitais semelhantes,
relatório executivo em PDF e modo apresentação para vídeo.

É um produto separado do GlosaAI. Divide com ele só o login, o cadastro de
tenants e o contrato de produtos, que moram no núcleo da plataforma. As decisões
de desenho estão em [ARQUITETURA.md](ARQUITETURA.md).

## Estado

| Etapa | O que entra | Situação |
|---|---|---|
| 1 | Contrato de produtos no núcleo, menu por contrato, esqueleto deste serviço com o login compartilhado | feito |
| 2 | Adapters SIH RD/RJ/ER, motivos e CNES; carga por UF (ou Brasil) no worker; organizações por planilha; ISGH; resumo por OSS | feito |
| 3 | Motor de oportunidades e benchmark por peer group | próxima |
| 4 | Painel da OSS, painel do hospital, modo apresentação | — |
| 5 | PDF executivo, prospecção, telas do SuperAdmin | — |
| 6 | SIA/SUS, SP do SIH, ticket e mix; demais UFs | — |

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # ajuste JWT_SECRET igual ao do núcleo
uvicorn app.main:app --port 8100 --reload
```

O núcleo (`glosa_ai`) precisa estar no ar em `CORE_API_URL`: sem ele, toda rota
protegida responde 503 — o serviço nega por padrão quando não consegue conferir
o contrato.

No frontend, defina `NEXT_PUBLIC_REVENUE_SCAN_URL` (padrão
`http://localhost:8100`). A entrada do produto é `/revenue-scan`.

## Carga de dados

Ver [CARGA_DATASUS.md](CARGA_DATASUS.md): qualquer UF ou o Brasil inteiro, e
qualquer organização gestora por planilha.

## Produção

O serviço sobe junto com o compose do núcleo (`glosa_ai/deploy`): o
`deploy.sh` acha este repositório ao lado do `glosa_ai` (ou em
`REVENUE_SCAN_DIR`), migra, cadastra as organizações e sobe `revenue-scan` e
`worker-revenue-scan`. O nginx do núcleo encaminha `/api/revenue-scan/` para
cá, no mesmo domínio da API.

No `.env` deste repositório no servidor: `APP_ENV=production`, o **mesmo**
`JWT_SECRET` do núcleo, `DATABASE_URL` do Postgres e `CORS_ORIGINS` com o
endereço do frontend. `CORE_API_URL` e `REDIS_URL` vêm do compose.

## Liberar para um tenant

SuperAdmin → Estabelecimentos → editar → **Produtos contratados** → Revenue Scan
SUS: status, vigência, módulos e escopo (CNES liberados, limite de CNES,
período, PDF, dados internos). A mudança vale aqui em até
`ENTITLEMENT_CACHE_SECONDS`.

## Testes

```bash
pytest
```

Nenhum teste usa banco ou núcleo reais: o núcleo é simulado e o banco é SQLite
em memória.

## Migrations

```bash
alembic upgrade head
```

As tabelas ficam no schema `DB_SCHEMA` (padrão `revenue_scan`) e a versão do
Alembic em `revenue_scan.revenue_scan_alembic`, separada da do núcleo.
# medops-revenue-scan
