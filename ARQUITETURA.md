# Arquitetura

## Decisões

**Repositório próprio.** O Revenue Scan processa arquivos do DATASUS de vários
estados e muda no ritmo da venda. No mesmo processo do GlosaAI, uma carga
pesada ou um erro dele afetaria clientes em produção. Aqui ficam os dados, as
regras e os jobs do Revenue Scan; nada disso importa código do GlosaAI.

**Login e contrato no núcleo.** Tenant, usuário, JWT e o contrato de produtos
(`platform_products`, `platform_product_modules`, `platform_product_features`,
`tenant_entitlements`) moram no núcleo da plataforma — hoje o pacote
`app/platform_core` do repositório `glosa_ai`, sem dependência do domínio do
GlosaAI, para poder ser extraído.

**Conferência por HTTP, negando por padrão.** Toda rota protegida:

1. valida o JWT com o segredo compartilhado (`JWT_SECRET`);
2. pergunta ao núcleo `GET /platform/me/entitlements/REVENUE_SCAN_SUS` com o
   token do próprio usuário;
3. aceita só `200` do mesmo tenant do token. `403`/`404` viram 403; erro ou
   núcleo fora do ar viram 503.

A resposta fica em cache por `ENTITLEMENT_CACHE_SECONDS` (padrão 60), pelo
hash do token. Suspender um contrato leva até esse tempo para valer aqui.

**Schema próprio.** Mesmo Postgres, schema `revenue_scan`, tabela de versão do
Alembic separada. O Revenue Scan não lê tabela do GlosaAI. Base de referência
que os dois usem (CNES, SIGTAP) entra por adapter, não por consulta à tabela do
outro.

**Público × privado.** Toda fonte declara `ClasseDado`: `PUBLICO` (DATASUS,
serve qualquer tenant) ou `PRIVADO` (arquivo do hospital, sempre com
`tenant_id` e nunca compartilhado). Leitura de dado privado gera evento de
auditoria no núcleo.

**Linguagem financeira.** Rejeição registrada no RJ/ER é
`CONFIRMED` — o SUS registrou. Quanto dela volta ao caixa é
`ESTIMATED_OPPORTUNITY` até dado interno confirmar. As telas e o PDF dizem
“receita rejeitada comprovada pelo SUS” e “oportunidade financeira estimada”,
nunca “dinheiro perdido”.

## Componentes

```
app/
  config.py        ambiente; recusa segredo fraco em produção
  security.py      leitura do JWT do núcleo
  entitlements.py  cliente do núcleo, com cache e negação por padrão
  api/deps.py      require_revenue_scan: token + contrato + mesmo tenant
  api/routes/      /health, /api/revenue-scan/me
  adapters/base.py DataSourceAdapter: SIH RD/RJ/ER/SP, SIA, CNES, SIGTAP, IBGE, Onco
  db.py            engine no schema próprio
alembic/           versão em revenue_scan.revenue_scan_alembic
```

Próximas peças, na ordem de entrega: `adapters/sih_*.py` e `jobs/` (carga por
UF), `domain/` (organizações, estabelecimentos, benchmark, oportunidades,
scans, prospects), `engine/` (RevenueOpportunityEngine e peer group) e
`reports/` (diagnóstico executivo em PDF).

## Frontend

As telas ficam no `glosaai-frontend`, que já tem login e componentes:

- `/revenue-scan` — entrada do produto (painel da OSS e do hospital a seguir);
- `/presentation/[organizationId]` — modo vídeo, sem menu;
- SuperAdmin → Estabelecimentos → editar → **Produtos contratados**.

O menu lê `GET /platform/me/entitlements`: item de produto sem contrato não
aparece, e a rota mostra “não está no contrato” em vez de abrir.
