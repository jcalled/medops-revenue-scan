# Ampliação do motor e fluxo de correção — somente SUS

## Cobertura do plano público

O catálogo passou de 4 para 13 códigos. Foram acrescentados os seis grupos solicitados:

| Grupo | Códigos |
| --- | --- |
| Diárias acima do período/mês | 060150, 060149 |
| Procedimentos incompatíveis | 060051 |
| OPM incompatível com realizado/especial | 060205, 060206 |
| Procedimento especial obrigatório ausente | 060171 |
| Número da AIH fora da faixa | 010003 |
| Duplicidade de identificação / sobreposição | 020077, 020081 |

Cada motivo expõe problema, campo/local a conferir, condição pretendida, ação condicional, justificativa e documentos. Motivos desconhecidos permanecem na análise; cobrir um motivo não libera a AIH inteira. Não há alteração automática de datas, identidade, autorização ou códigos para contornar críticas.

As descrições foram conferidas nos relatórios públicos de críticas da SMS Rio: [junho/2025](https://saude.prefeitura.rio/wp-content/uploads/sites/47/2025/07/Totais-de-erros-CNES-062025.pdf), [março/2025](https://saude.prefeitura.rio/wp-content/uploads/sites/47/2025/04/Total-de-erros-CNES-032025.pdf), [julho/2025](https://saude.prefeitura.rio/wp-content/uploads/sites/47/2025/08/Totais-de-erros-MUNICIPIO-072025.pdf). As condutas propostas são interpretações operacionais condicionais da MedOps, não homologação automática pelo gestor.

## Fluxo operacional implementado

No FaturaSUS, após importar a conta ou abrir uma análise:

1. Ver o original preservado, checksum e campos suportados.
2. Adicionar alterações com AIH, campo, antes, depois, justificativa e referência documental.
3. Confirmar explicitamente a conferência documental e o aceite.
4. Gerar outra análise/versão sem sobrescrever o original. O FaturaSUS roda novamente.
5. Conferir o antes/depois, autor/data do aceite, falhas, avisos, verificações não executadas e alertas de lote.
6. Baixar o arquivo intermediário corrigido.
7. Registrar o protocolo de apresentação feita externamente no sistema do hospital.
8. Importar um retorno e relacioná-lo por AIH e competência à versão. O arquivo original do retorno e seu hash são preservados no registro privado; o conteúdo bruto não é exposto na resposta de resumo.

O novo fluxo usa somente rotas `/api/fatursus/correcoes`. Não altera o motor nem a tela de correções de convênios. As mudanças de convênios já existentes na área de trabalho são de etapas anteriores desta conversa.

## Limite técnico que permanece

O parser existente usava um layout delimitado interno e o apresentava como SISAIH01 oficial. A identificação foi corrigida no módulo SUS. O formato implementado é `MEDOPS_AIH_DELIMITADO_V1`, documentado no frontend em `/formatos/medops-aih-v1.txt`.

Este arquivo não é uma remessa oficial SISAIH01. O formato oficial de posições fixas não foi implementado/homologado nesta entrega. Para fechar essa etapa, é necessário conferir o layout vigente do sistema de faturamento/SISAIH01 utilizado pelo hospital e testar a importação nele. As páginas oficiais de documentação consultadas não puderam ser carregadas nesta execução; não se adotou um layout antigo como se fosse vigente.

A edição não modifica identificação, número da AIH, datas assistenciais, autorizações ou CNES. Pendências de faixa, duplicidade e gestor têm orientação de tratamento e rastreabilidade, não renumeração automática. OPM existente pode ter código/quantidade revisados; não há criação arbitrária de novos blocos. Procedimentos secundários usam os cinco campos existentes do formato intermediário.

O aceite documental é uma declaração do operador, não verificação automática do prontuário. Bases ausentes continuam como SKIPPED. A ausência de um achado não vira PASS: a comparação mostra “sem confirmação” quando não existe aprovação explícita da mesma regra. O protocolo externo é declaratório. Aprovação relacionada não comprova pagamento nem causalidade de uma alteração. Nenhum evento financeiro de recuperação é gerado por esse fluxo.

## Integridade e acesso

- Tenant e produto `fatursus` são obrigatórios na seleção da análise; IDs de outro cliente ou do produto de convênios retornam 404.
- Comparação do checksum e do valor anterior antes de aplicar.
- Bloqueio de destino repetido, AIH duplicada, campo desconhecido, caracteres de separação injetados e valores incompatíveis com o formato.
- Alterações preservam os demais bytes, separador e quebras de linha; o original permanece intacto.
- Repetição do mesmo pedido para a mesma origem devolve a versão já gerada.
- Retorno exige envio registrado, situação explícita, AIH da versão e competência correspondente; conflito ou duplicação do arquivo é recusado.

## Validação e ativação

Suítes Revenue Scan e FaturaSUS executadas, incluindo o fluxo com pipeline real e dados isolados de teste. TypeScript, compilação Next e ESLint dos componentes novos passaram. Dados de demonstração são sintéticos. O fluxo visual de edição, aceite, geração de versão, protocolo e retorno passou no Chrome, em desktop e celular, sem erros de JavaScript ou transbordamento horizontal. As suítes concluíram 149 testes no Revenue Scan e 189 no FaturaSUS.

Alterações locais, sem implantação ou transmissão ao SUS. O novo fluxo operacional reutiliza as tabelas de análise e eventos existentes, sem nova migração no núcleo. A tela de histórico público do Revenue Scan continua dependendo da migração `0010_evidencia_rd`, introduzida na etapa anterior.
