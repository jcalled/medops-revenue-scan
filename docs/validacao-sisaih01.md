# Validação da interface SISAIH01

Status: pendente de layout da versão alvo e teste no aplicativo. Não há exportador oficial homologado.

## Resultado da investigação

O formato atualmente suportado pelo FaturaSUS é MEDOPS_AIH_DELIMITADO_V1. Ele não pode ser renomeado ou convertido por simples separação de campos para uma remessa oficial.

O [anexo oficial de 2012](https://bvsms.saude.gov.br/bvs/saudelegis/sas/2012/anexo/anexo_prt0133_23_02_2012.pdf) descreve posições fixas. Exemplos: lote nas posições 1–8, apresentação 12–17, órgão emissor 21–30, CNES 31–37 e AIH 44–56. Esta referência é histórica; não comprova o layout vigente ou a compatibilidade com o aplicativo do hospital.

As páginas de [documentos](https://sihd.datasus.gov.br/documentos/documentos_sisaih01.php) e [versões](https://sihd.datasus.gov.br/versao/versao_sisaih01.php) não responderam nas tentativas desta execução, incluindo consulta HTTP direta com limite de 25 segundos.

## Insumos mínimos para concluir

1. Nome e versão do sistema de faturamento e versão do SISAIH01 de destino.
2. Documento de layout correspondente à versão, preferencialmente obtido no próprio aplicativo ou portal oficial.
3. Arquivo sintético exportado pelo sistema de origem, preservando posições, tamanhos, blocos e codificação; não preencher dados ausentes a partir de inferência sobre o paciente.
4. Ambiente de teste do aplicativo de destino, operado pelo hospital ou disponibilizado para validação autorizada.

## Critérios de implementação e aceite

- Identificar separadamente a interface de importação para o SISAIH01 e a remessa de saída para o gestor/SIHD; comprovar qual delas foi implementada.
- Mapear todos os campos e blocos repetidos do layout alvo; registrar origem documental, versão e hash da especificação.
- Preservar original, campos não alterados e zeros à esquerda. Bloquear truncamento, campos obrigatórios ausentes, versão desconhecida e incompatibilidade de tipos.
- Testar leitura e escrita com arquivos sintéticos independentes do gerador, incluindo limites dos blocos, codificação, datas e quebras de linha.
- Importar o arquivo de teste no aplicativo alvo e guardar relatório de críticas, versão do aplicativo e hash do arquivo efetivamente importado. Um teste unitário local não substitui esse passo.
- Confirmar a consistência e, quando aplicável, a geração da remessa pelo aplicativo oficial. Não transmitir produção durante a homologação.

## Evidências distintas

Compatibilidade de arquivo: comprovada pelo teste de importação na versão alvo.

Aprovação da conta: comprovada pelo retorno real do processamento, relacionado à AIH, competência e apresentação. Importação bem-sucedida não prova aprovação.

Pagamento: exige conciliação com documento financeiro do hospital/gestor. Valor aprovado em base pública não deve ser convertido automaticamente em dinheiro recebido ou recuperação atribuída à MedOps.

O fluxo atual de versões e retorno permanece disponível, com exportação intermediária. Não houve liberação de remessa oficial nem alteração no produto de convênios nesta investigação.
