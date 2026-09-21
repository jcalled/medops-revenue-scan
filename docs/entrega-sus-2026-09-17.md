# Entrega exclusivamente SUS — revisão de 17/09/2026

## Reversões concluídas

Restaurados byte a byte à versão HEAD dos respectivos repositórios:

- glosa_ai/app/core/pagamento/atribuicao.py
- glosa_ai/app/services/homologacao/confiabilidade.py
- glosa_ai/tests/glosaai/test_pagamento.py
- glosaai-frontend/src/app/(app)/glosaai/dashboard/page.tsx
- glosaai-frontend/src/app/(app)/glosaai/quality-ai/page.tsx

Removido tests/glosaai/test_atribuicao_escopo.py, teste adicionado exclusivamente para o comportamento revertido. A reversão restaura o produto anterior; não representa nova certificação da lógica financeira de convênios. As cópias das alterações retiradas estão em /tmp/medops-reversao-convenios-20260917 nesta máquina.

## Complementos SUS

- Cada AIH solicita o contexto SIGTAP da própria competência. A troca preserva os acumuladores de diárias do lote. Falha ou divergência de base continua pendente.
- A carga SIGTAP valida a competência contra o arquivo antes de alterar tabelas.
- Cargas processadas pelo importador novo recebem a revisão SIGTAP_RELACOES_V2. O atualizador detecta cargas antigas e reimporta mesmo quando o arquivo de procedimentos não mudou. Sem o arquivo de relações, não registra a revisão como completa.
- Mantidos editor de versões, referências antes/depois, evidências públicas e migração 0010_evidencia_rd.

## Verificação local

- 220 testes em FaturaSUS, relações SIGTAP e pagamento de convênios restaurado.
- 3 novos testes de prontidão: carga antiga, divergência de competência e troca de tabela sem perder acumuladores.
- 149 testes Revenue Scan.
- Build Next/TypeScript e diff sem erros de whitespace.
- Nenhum comando contra banco de produção, implantação ou transmissão de AIH executado.

## Sequência para homologação e publicação

1. Revisar e versionar todos os arquivos do manifesto arquivos-entrega-sus-2026-09-17.json, incluindo os novos arquivos ainda não rastreados. Não publicar apenas o diff dos rastreados.
2. Remover credenciais em texto aberto da documentação de implantação e substituí-las nos provedores/serviços correspondentes. O README de deploy contém esse material; os valores não estão reproduzidos aqui. Excluir texto não revoga credencial nem limpa histórico Git.
3. Preparar backup do banco e imagem anterior. Em homologação, construir o núcleo, Revenue Scan, worker e frontend da mesma entrega.
4. Aplicar alembic upgrade head no Revenue Scan. A alteração 0010 adiciona campos_publicos. O deploy/deploy.sh existente já executa a migração do Revenue Scan quando encontra seu repositório; conferir esse caminho antes de executar.
5. Disponibilizar o volume revenue-evidence: escrita no worker, leitura na API. Verificar que os documentos novos são arquivados e podem ser recuperados pela tela. Históricos antigos só ganham novos campos públicos após recarga da fonte correspondente.
6. Reimportar SIGTAP por competência necessária. Para o pacote já disponível no diretório configurado, o atualizador do núcleo aceita: python scripts/cron/atualiza_referencias.py --only sigtap --force --sem-download. Conferir a competência do pacote; este comando não baixa automaticamente todos os meses históricos. Reiniciar processos para limpar caches após a carga.
7. Validar login/contrato SUS, importação de arquivo intermediário, edição, criação de versão, evidência, download, registro de protocolo e retorno em homologação. Conferir isolamento entre clientes e indisponibilidade do produto não contratado.
8. Publicar somente após essa verificação. O núcleo é compartilhado fisicamente; mesmo alterações funcionais SUS exigem teste de fumaça dos endpoints existentes.

## Limites que não foram eliminados

A exportação permanece MEDOPS_AIH_DELIMITADO_V1. Layout vigente e importação no SISAIH01 do hospital continuam pendentes. Não há prova de pagamento nem autorização de transmissão ao SUS gerada por esta entrega. O acompanhamento de retorno não converte automaticamente aprovação em receita recuperada.
