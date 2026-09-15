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
| 3 | Leitos e habilitações do CNES; mix de procedimentos; semelhantes por UF, porte, natureza e faixa de alta complexidade; RevenueOpportunityEngine; score; rotas de scan do hospital e ranking da OSS | feito |
| 4 | Painel da OSS (`/revenue-scan`), scan do hospital (`/revenue-scan/hospital?cnes=`) e modo apresentação (`/presentation?org=`) no `glosaai-frontend` | feito |
| 5 | Prova AIH por AIH; modelo híbrido; dados do DATASUS pela tela; recuperação com fatura e linha de base; prospecção (CRM); cadastro de organizações; relatório executivo em PDF (`/relatorio`) | feito |
| 6 | SIA/SUS, SP do SIH, ticket e mix | — |

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

## Recortes

Qualquer conjunto de hospitais do SUS carregados, dentro do escopo do contrato:

- `GET /api/revenue-scan/filters` — estados, municípios, naturezas, gestões,
  portes e organizações que existem nos dados, com quantos hospitais cada um tem;
- `GET /api/revenue-scan/hospitals` — lista paginada e ordenável;
- `GET /api/revenue-scan/panorama` — o painel executivo do recorte.

Filtros em comum: `organizacao`, `uf`, `municipio` (código do CNES),
`natureza` (`MUNICIPAL` prefeitura, `ESTADUAL`, `FEDERAL`, `EMPRESA_PUBLICA`,
`FILANTROPICO`, `PRIVADO`), `gestao` (`MUNICIPAL`, `ESTADUAL`), `porte`, `q`
(nome ou CNES) e `cnes` (seleção separada por vírgula). `ordem`: `score`,
`confirmado`, `sinais`, `apresentado`, `nome`.

Natureza vem da natureza jurídica do CNES (de quem é o hospital); gestão, da
esfera administrativa (quem faz a gestão do contrato SUS). Hospital estadual
gerido por OSS é `ESTADUAL` e aparece na OSS pelo vínculo cadastrado.

## Carga de dados

Ver [CARGA_DATASUS.md](CARGA_DATASUS.md): qualquer UF ou o Brasil inteiro, e
qualquer organização gestora por planilha. Pela tela, a administração da
plataforma usa **Dados do DATASUS** (`/revenue-scan/dados`): UFs, meses
publicados, fila do worker e histórico de arquivos.

Medido: um mês de SP (253.871 AIH aprovadas, 607 hospitais) carrega em 47 s com
pico de 518 MB; o recálculo leva 4,6 s. O `worker-revenue-scan` tem 2 GB.

## Prova AIH por AIH

`GET /api/revenue-scan/hospitals/{cnes}/aih-rejeitadas`: cada AIH rejeitada do
hospital marcada como **entra na recuperação**, **não entra** (bloqueio do gestor
ou motivo sem regra) ou **já voltou aprovada**, com motivo oficial, o porquê e o
arquivo RJ/ER de origem com SHA-256. A oportunidade confirmada nunca passa da
soma das AIH marcadas, nem no total nem no mês.

## Recuperação e fatura

`/api/revenue-scan/recovery` (tela `/revenue-scan/recuperacao`). O acompanhamento
marca as AIH corrigíveis ainda não recebidas — BASE (rejeitadas antes do início)
e NOVA (durante o contrato) — e, a cada carga, as que voltam aprovadas depois da
rejeição viram **recuperadas** com o valor aprovado no RD.

Fatura do mês: fixo por hospital + percentual **só sobre o excedente da linha de
base**, hospital por hospital. A linha de base é o que o hospital já recuperava
sozinho por mês: média dos até seis meses de processamento anteriores ao início
(cada um com dois meses carregados antes), ou o valor negociado
(`PATCH linha_de_base`). Sem meses anteriores carregados ela fica zero e a tela
avisa — carregue ao menos seis meses antes do início.

## Prospecção e organizações

Só administração da plataforma.

- `/api/revenue-scan/crm/prospects` (tela `/revenue-scan/prospeccao`): importa a
  planilha de prospecção (`POST .../import` ou `python -m app.seed.prospeccao
  planilha.xlsx`), etapas com histórico, notas e proposta. Reimportar atualiza
  os dados públicos e preserva o andamento.
- `POST .../prospects/{id}/organization` cria a organização gestora;
  `GET .../prospects/{id}/suggestions` sugere CNES pelas unidades citadas.
- `/api/revenue-scan/organizations` e `/establishments` (tela
  `/revenue-scan/organizacoes`): cadastro, vínculo, confirmação e busca de
  hospitais por nome ou CNES.

## Produção

O serviço sobe junto com o compose do núcleo (`glosa_ai/deploy`): o
`deploy.sh` acha este repositório ao lado do `glosa_ai` (ou em
`REVENUE_SCAN_DIR`), migra, cadastra as organizações e sobe `revenue-scan` e
`worker-revenue-scan`. O nginx do núcleo encaminha `/api/revenue-scan/` para
cá, no mesmo domínio da API.

No `.env` deste repositório no servidor: `APP_ENV=production`, o **mesmo**
`JWT_SECRET` do núcleo, `DATABASE_URL` do Postgres e `CORS_ORIGINS` com o
endereço **do frontend** (ex.: `https://www.glosaai.com.br`), não o da API.
`CORE_API_URL` e `REDIS_URL` vêm do compose.

Conexões: o Postgres gerenciado tem limite e o GlosaAI divide o mesmo cluster.
Use um **connection pool da DigitalOcean em modo Transaction** só para o Revenue
Scan (porta 25061, o nome do pool no lugar do banco na URL). O serviço funciona
atrás dele: o schema entra na compilação das queries (`schema_translate_map`) e
as migrations usam `SET LOCAL` numa transação só. `DB_POOL_SIZE` e
`DB_MAX_OVERFLOW` (padrão 2 e 1, por processo) limitam o lado do serviço. O
container não migra ao subir; quem migra é o `deploy.sh`.

O nginx do droplet roda fora do Docker: o serviço publica `127.0.0.1:8110` e o
site da API encaminha `/api/revenue-scan/` para lá (`glosa_ai/deploy/nginx.conf`).
Mudança no `.env` só vale com `docker compose ... up -d --force-recreate`.

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
