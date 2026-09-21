# SIGTAP: correções e evidência no SUS

## Implementado

- QT_DIAS_PERMANENCIA preservado como média nos carregadores de arquivo e banco. Não gera limite máximo nem aprovação de limite que não foi verificado.
- Quantidade OPM por par procedimento/material; soma unidades em linhas repetidas. Limite indisponível fica SKIPPED, sem usar o maior limite de outro material.
- Importador conserva TP_COMPATIBILIDADE=5 como OBRIGATORIA. Consultas de compatibilidade incluem esta relação, mas o motor apresenta sua exigência separadamente para conferência documental. Não preenche automaticamente procedimentos ausentes; alternativas e condições exigem análise.
- Sugestões exigem competência explícita (aceita MMAAAA e AAAAMM). Não recorrem ao mês mais recente. O pipeline suspende verificações SIGTAP quando a competência carregada diverge da conta. O arquivo local identifica o mês pelo próprio conteúdo.
- Novas versões de correção guardam uma fotografia das referências consultadas antes/depois: AIH, procedimento, competência, média, relações, quantidades e links oficiais. Base ausente aparece pendente. Versões antigas não ganham evidência retroativa inventada.

## Ativação e limites

Reimportar os pacotes SIGTAP das competências utilizadas e reiniciar os processos/caches após implantação: cargas antigas perderam a distinção de obrigatoriedade. Não é possível reconstruí-la com segurança apenas pelo banco antigo. Não houve carga em produção nem implantação nesta execução.

O teste de competência impede aplicar o mês errado; não instala pacotes históricos ausentes. Relações obrigatórias geram orientação de conferência, não garantia de aprovação. A documentação explicativa não substitui portarias, condições específicas e comprovação assistencial. As relações exibidas são referentes ao procedimento principal da conta; este incremento não constitui validação exaustiva de todas as combinações entre procedimentos especiais.

Arquivos em services/reference alterados nesta etapa tratam exclusivamente dados SIGTAP. Nenhuma regra ou tela de convênios foi alterada. A suíte SIGTAP existente está historicamente localizada em tests/glosaai, mas testa a carga SUS.

## Fontes

- https://wiki.saude.gov.br/sigtap/index.php/Gerais
- https://wiki.saude.gov.br/sigtap/index.php/M%C3%B3dulo_Relat%C3%B3rios_de_Compatibilidades

## Verificação

201 testes: suíte FaturaSUS e testes existentes de carga SIGTAP; TypeScript, ESLint do componente e build Next passaram. Os testes novos cobrem média versus máximo, unidades repetidas de OPM, limite agregado legado, competência ausente/divergente, relações obrigatórias e consulta da competência no banco SQLite isolado.
