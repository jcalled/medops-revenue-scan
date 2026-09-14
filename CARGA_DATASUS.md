# Carga do DATASUS

O Revenue Scan funciona para qualquer hospital do SUS e qualquer organização
gestora do país. A unidade é o CNES; a carga é por UF.

## O que é carregado

| Fonte | Arquivo | O que vira |
|---|---|---|
| SIH/SUS RD | `RD{UF}{AAMM}.dbc` | produção aprovada por hospital e mês; número de cada AIH aprovada |
| SIH/SUS RJ | `RJ{UF}{AAMM}.dbc` | AIH rejeitada, com valor, datas e procedimento |
| SIH/SUS ER | `ER{UF}{AAMM}.dbc` | motivo oficial de cada rejeição |
| TAB_SIH | `Auxiliar/TAB_SIH.zip` | descrição dos motivos (tabela 0027) |
| CNES | API de dados abertos | nome fantasia, município, CNPJ, natureza jurídica |

Todos em `ftp://ftp.datasus.gov.br/dissemin/publicos/SIHSUS/200801_/`, menos o
CNES (`https://apidadosabertos.saude.gov.br/cnes/estabelecimentos/{cnes}`).

`AAMM` é o mês de **processamento**. A AIH de março processada e rejeitada em
maio fica em maio; o mês de atendimento está em `competencia_aih`.

## Regras de contagem

- Cada AIH conta uma vez por arquivo; havendo linhas repetidas, a última vale.
- Uma competência entra inteira (RD, RJ e ER juntos) ou não entra. Recarregar
  substitui.
- **Taxa bruta**: rejeitado ÷ (aprovado + rejeitado) em cada processamento.
- **Perda líquida**: cada AIH rejeitada uma vez, se não aparece aprovada em
  nenhum processamento carregado. AIH paga e rejeitada na reapresentação não
  conta.

Conferido contra o painel do Ceará (mai–jul/2026): mesmas contagens de AIH
aprovadas e rejeitadas no estado (161.847 e 11.903) e nos 8 hospitais do ISGH,
mesmos valores do ISGH ao centavo.

## Comandos

```bash
# Uma UF, as 3 competências mais recentes publicadas
python -m app.jobs.carga_sih --uf CE

# Várias UFs, ou o Brasil inteiro
python -m app.jobs.carga_sih --uf CE,PE,ES,GO
python -m app.jobs.carga_sih --uf TODAS --quantidade 12

# Competências específicas
python -m app.jobs.carga_sih --uf SP --competencias 202605,202606,202607

# Arquivos já baixados numa pasta (sem FTP)
python -m app.jobs.carga_sih --uf CE --pasta /dados/sih --sem-cnes
```

Uma UF que falha (arquivo ainda não publicado, FTP instável) não para as
outras: o comando termina com a lista das que falharam e cada tentativa fica em
`data_loads`.

Pela fila, no worker (`worker-revenue-scan`):

```python
from app.jobs.fila import enfileirar_carga_uf
enfileirar_carga_uf("SP", quantidade=12)
```

## Tempo e espaço

Ceará, 3 competências (≈ 170 mil AIH, 232 hospitais): 55 s, incluindo a
consulta dos 232 nomes na API do CNES. Os arquivos são baixados para uma pasta
temporária e apagados ao fim de cada competência; o que fica no banco são os
agregados por hospital, as rejeições com motivo e o número das AIH aprovadas.
São Paulo tem cerca de quatro vezes o volume do Ceará.

## Organizações gestoras

Hospital estadual gerido por OSS aparece no CNES com o CNPJ da secretaria.
O vínculo com a OSS é cadastrado com fonte:

```bash
# As conferidas no código (hoje, o ISGH com 8 hospitais)
python -m app.seed.organizacoes

# Achar o CNES de uma unidade pelo nome, entre os hospitais carregados
python -m app.seed.organizacoes --buscar "URGENCIAS" --uf GO

# Qualquer OSS, por planilha (uma linha por hospital)
python -m app.seed.organizacoes --csv oss.csv
```

Colunas: `sigla, nome, cnpj, uf, site, fonte, cnes, sigla_unidade, situacao,
verificado_em`. Use `CONFIRMADO` só depois de conferir a fonte; o padrão é
`A_CONFIRMAR`. Organização sem CNES na planilha entra sem hospitais — o
cadastro fica pronto para o mapeamento.

## Resumo

`GET /api/revenue-scan/organizations/{id}/summary` devolve totais, meses,
hospitais (com nome, sigla, perda líquida e principais motivos) e a ressalva de
dado público, dentro do escopo do contrato do tenant.
