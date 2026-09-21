"""Condutas condicionais, sem substituir documentação clínica ou decisão do gestor."""
FONTE = "https://saude.prefeitura.rio/wp-content/uploads/sites/47/2025/07/Totais-de-erros-CNES-062025.pdf"


def regra(titulo, campo, onde, problema, depois, acao, porque, documentos, fonte=FONTE):
    return dict(titulo=titulo, campo=campo, onde=onde, problema=problema, depois=depois,
                acao=acao, porque=porque, documentos=documentos, fonte=fonte)


REGRAS_AMPLIADAS = {
    "060150": regra(
        "Diárias acima do período de internação", "Diárias por competência", "Conta da AIH e censo",
        "As diárias superaram o período de internação considerado na competência informada.",
        "Quantidades sustentadas pelo censo, pelo tipo de diária e pelas regras da competência.",
        "Confrontar diárias e permanência na competência. Corrigir lançamento indevido quando comprovado; não alterar datas reais para acomodar a cobrança.",
        "A diferença entre datas é um sinal para revisão. A quantidade correta depende da contagem aplicável ao tipo de diária e do atendimento documentado; reduzir ao máximo permitido não basta para comprovar a conta.",
        ["Diárias discriminadas da conta", "Censo e datas documentadas", "Regra de contagem da competência"]),
    "060149": regra(
        "Diárias acima dos dias do mês", "Diárias e competência do lançamento", "Conta da AIH",
        "O total de diárias informado superou os dias do mês de referência.",
        "Lançamentos distribuídos conforme o atendimento e as regras válidas para cada competência.",
        "Conferir mês, quantidade e eventuais lançamentos repetidos. Ajustar somente a divergência comprovada, preservando as datas assistenciais.",
        "Corrigir competência ou quantidade digitada indevidamente pode remover o excesso; não autoriza deslocar produção para um mês conveniente.",
        ["Conta original com competência e diárias", "Censo", "Memória de cálculo das diárias"]),
    "060051": regra(
        "Procedimentos incompatíveis entre si", "Procedimento principal e realizado", "Conta da AIH e SIGTAP",
        "O procedimento realizado foi apontado como incompatível com o principal.",
        "Códigos que representem o atendimento realizado e satisfaçam as relações da competência.",
        "Conferir ambos os códigos e a relação SIGTAP aplicável. Se houver erro de codificação documentado, corrigir o campo correspondente; se os atos não forem compatíveis, encaminhar à auditoria.",
        "A lista de códigos compatíveis limita as opções, mas não identifica sozinha o procedimento realizado. O código escolhido precisa corresponder ao registro assistencial.",
        ["Procedimentos enviados", "Descrição do atendimento", "Relação SIGTAP da competência"]),
    "060205": regra(
        "OPM incompatível com o procedimento realizado", "Código da OPM e procedimento relacionado", "Itens da conta e SIGTAP",
        "A OPM lançada não foi reconhecida como compatível com o procedimento realizado.",
        "Material efetivamente utilizado, corretamente codificado e relacionado ao procedimento elegível.",
        "Conferir material, procedimento e relação SIGTAP. Corrigir erro de código ou vínculo apenas com comprovação do material utilizado; encaminhar incompatibilidade real para revisão da cobrança.",
        "A compatibilidade deve existir entre o material usado e o procedimento. Trocar por um material permitido que não foi utilizado produziria uma cobrança incorreta.",
        ["Itens OPM e quantidades da conta", "Registro de utilização do material", "Relação OPM/procedimento da competência"]),
    "060206": regra(
        "OPM incompatível com procedimento especial", "OPM e procedimento especial relacionado", "Itens da conta e SIGTAP",
        "A OPM foi apontada como incompatível com o procedimento especial informado.",
        "OPM utilizada relacionada ao procedimento especial documentado, conforme a competência.",
        "Conferir o vínculo com o procedimento especial e a relação SIGTAP específica. Não usar a compatibilidade com o principal como substituta dessa verificação.",
        "O vínculo errado pode causar a rejeição mesmo quando o material existe na tabela. A correção exige comprovar qual ato recebeu o material.",
        ["OPM e procedimento especial enviados", "Registro assistencial de utilização", "Relação SIGTAP específica da competência"]),
    "060171": regra(
        "Procedimento especial obrigatório ausente", "Procedimentos especiais da AIH", "Conta da AIH e SIGTAP",
        "O processamento exigiu o lançamento de procedimento especial compatível.",
        "Procedimento exigido lançado somente quando realizado, documentado e elegível.",
        "Identificar a exigência na competência e conferir se o ato foi realizado. Se houve omissão de lançamento, incluir com evidência. Se o ato não ocorreu, não acrescentar um procedimento apenas para passar na regra.",
        "A proposta resolve uma omissão de faturamento quando o atendimento exigido realmente ocorreu. A exigência da tabela não comprova execução.",
        ["Procedimentos principais e especiais enviados", "Registro de execução do ato", "Exigência SIGTAP da competência"]),
    "010003": regra(
        "Número da AIH fora da faixa autorizada", "Número da AIH e faixa autorizada", "Regulação e gestor",
        "O número da AIH foi apontado como fora da faixa autorizada.",
        "Autorização e numeração conferidas com o gestor, com vínculo preservado à conta de origem.",
        "Conferir autorização, faixa, hospital e competência. Solicitar ao gestor a regularização apropriada; não gerar ou substituir o número por conta própria.",
        "Um número numericamente dentro da faixa não comprova autorização. A regularização precisa preservar a rastreabilidade e evitar duplicidade de cobrança.",
        ["AIH e autorização originais", "Faixa atribuída ao hospital", "Orientação ou autorização do gestor"],
        "https://saude.prefeitura.rio/wp-content/uploads/sites/47/2025/04/Total-de-erros-CNES-032025.pdf"),
    "020081": regra(
        "Internações sobrepostas", "Períodos e contas do mesmo paciente", "Faturamento e auditoria do gestor",
        "O processamento identificou períodos de internação sobrepostos no movimento.",
        "Contas e períodos conciliados, com transferências, continuidade e eventuais duplicidades esclarecidas.",
        "Confrontar as contas envolvidas e os registros de admissão, alta e transferência. Corrigir duplicação de lançamento comprovada ou encaminhar o caso legítimo à auditoria. Não mudar datas assistenciais para remover o alerta.",
        "A sobreposição é um indício que exige contexto. A investigação distingue duplicidade de situações assistenciais que demandam análise do gestor.",
        ["Contas e retornos anteriores", "Admissão, alta e transferência documentadas", "Identificação conferida do paciente e parecer do gestor"]),
    "020077": regra(
        "Bloqueio por duplicidade de CNS/CPF", "Identificação do paciente e contas relacionadas", "Cadastro e auditoria do gestor",
        "O processamento bloqueou a AIH por duplicidade de identificação do paciente.",
        "Identidade e contas conciliadas, sem substituição indevida de CNS ou CPF.",
        "Conferir documentos e contas relacionadas. Corrigir erro cadastral comprovado e solicitar análise do bloqueio quando necessário; não trocar a identidade para contornar a crítica.",
        "A correção deve restabelecer a identidade correta e esclarecer a duplicidade, não apenas produzir um identificador que passe na validação.",
        ["Identificação validada do paciente", "Contas e retornos relacionados", "Relatório de críticas e decisão da auditoria"],
        "https://saude.prefeitura.rio/wp-content/uploads/sites/47/2025/08/Totais-de-erros-MUNICIPIO-072025.pdf"),
}
