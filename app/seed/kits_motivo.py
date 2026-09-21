"""
Kits iniciais dos motivos de rejeição que concentram o valor.

Escolhidos pelos 30 motivos de maior valor no Ceará (mai–jul/26): cobrem 96%
do valor rejeitado e 94% do recuperável. Todos entram A_CONFIRMAR: a leitura é
da MedOps, a partir da descrição oficial do motivo e da prática de faturamento,
e só muda a classificação das AIH depois de confirmada com a secretaria ou o
manual do SIHD. Onde a classe proposta difere do tipo de rejeição, o kit diz.

Rodar de novo só inclui o que falta: não apaga o que foi editado pela tela.

    python -m app.seed.kits_motivo
"""
from __future__ import annotations

from typing import Any

FONTE = (
    "Leitura MedOps da descrição oficial do motivo (tabela de críticas do SIH) e da prática de faturamento, "
    "set/2026. Confirmar com a secretaria ou o manual do SIHD antes de marcar como confirmado."
)

SISAIH = "Arquivo do SISAIH01 da competência (movimento exportado), para ver a AIH como foi enviada"
CRITICAS = "Relatório de críticas do SIHD que a secretaria devolve ao hospital"
CNES_PROF = "CNES local: profissionais, vínculos, CBO e carga horária na competência"
ESCALA = "Escala da equipe no dia do procedimento (quem de fato executou)"
PRONTUARIO = "Prontuário das AIH a corrigir: datas, evolução, descrição cirúrgica, prescrições"
LEITOS = "CNES local: leitos existentes, SUS e em funcionamento, por tipo"
CENSO = "Censo diário de leitos (ocupação dia a dia) da competência"
HABILITACOES = "Portarias de habilitação vigentes e pedidos em andamento na secretaria"
FAIXAS = "Faixas de numeração de AIH autorizadas pela secretaria para o hospital"
CONTRATO = "Contrato com o gestor: metas, teto financeiro mensal e aditivos"
CNS = "CNS do paciente conferido no cartão ou no CadSUS"

REAPRESENTAR = (
    "Reapresentar no SISAIH01 até o 6º mês contado do mês da alta (alta em janeiro: até junho), se a AIH foi "
    "apresentada e rejeitada dentro dos quatro meses (MTO SIH jan/2017, item 4; Portaria SAES/MS 1.110/2021)."
)
NUNCA_INVENTAR = "Corrigir só o que o prontuário sustenta: nunca mudar o que foi feito no paciente para a conta passar."
SEM_MUDAR_DATAS = (
    "Não mudar data de internação nem de saída: AIH reapresentada com datas alteradas não é aceita "
    "(MTO SIH jan/2017, item 58)."
)

# Fontes oficiais (DATASUS, http://sihd.datasus.gov.br/documentos/documentos_sihd2.php).
MTO = "Manual Técnico Operacional do SIH, jan/2017 (MS/SAES)"
FONTE_PRAZO = f"{MTO}, item 4; Portaria SAES/MS 1.110/2021, consolidada na PRC SAES/MS 1/2022."
FONTE_CAPACIDADE = (f"{MTO}, item 59.1 (AIH rejeitada por capacidade é cancelada e não pode ser reapresentada); "
                    "Nota explicativa CGSI/DRAC/SAS/MS de 31/10/2012 (cálculo da capacidade instalada).")
FONTE_PROFISSIONAL = ("Orientações do SIHD2 sobre rejeição de profissionais (DATASUS) e Perguntas frequentes do SIHD2, "
                      "item 18: o SIHD confere o CNES da competência; corrigido o cadastro, o gestor importa os "
                      "profissionais de competências passadas e a AIH reapresentada pode ser aprovada.")
FONTE_HABILITACAO = ("Perguntas frequentes do SIHD2 (DATASUS), item 17: a habilitação é conferida na competência da "
                     "alta do paciente.")
FONTE_SERVICO = ("Perguntas frequentes do SIHD2 (DATASUS), item 12: o serviço/classificação precisa ser hospitalar "
                 "SUS, e próprio ou terceiro na AIH tem de bater com o CNES.")
FONTE_BLOQUEIO = f"{MTO}, itens 57 e 58 (liberação de crítica e AIH bloqueadas para análise do gestor)."


def _kit(codigo: str, titulo: str, significado: str, classe: str, onde: str, passos: list[str],
         dados: list[str], evidencias: list[str], prevencao: str | None = None, fonte: str = FONTE) -> dict[str, Any]:
    return {
        "codigo": codigo, "titulo": titulo, "significado": significado, "classe": classe, "onde_corrigir": onde,
        "passos": passos, "dados_do_hospital": dados, "evidencias": evidencias, "prevencao": prevencao,
        "fonte": fonte,
    }


def _leito(codigo: str, tipo: str) -> dict[str, Any]:
    return _kit(
        codigo, f"Hospital sem leitos de {tipo} no CNES",
        f"A AIH cobra diária de {tipo}, mas o CNES do hospital na competência não tem esse leito cadastrado "
        "(ou não tem a habilitação que o leito exige).",
        "MEDIA", "CNES",
        [
            f"Conferir no CNES da competência se há leitos de {tipo} cadastrados e habilitados.",
            "Se o leito existe, funciona e tem habilitação, atualizar o CNES e reapresentar.",
            f"Se o paciente ficou em outro tipo de leito, corrigir a diária na AIH conforme o prontuário.",
            f"Se o hospital não tem {tipo}, não reapresentar essa diária: tratar a habilitação com a secretaria.",
            REAPRESENTAR,
        ],
        [LEITOS, HABILITACOES, SISAIH, PRONTUARIO],
        ["Espelho do CNES antes e depois", "Portaria de habilitação do leito", "Folha de evolução do leito ocupado"],
        "O FaturaSUS confere, antes do envio, as diárias de UTI/UCI contra os leitos e habilitações do CNES.",
    )


def _profissional(codigo: str, titulo: str, significado: str, classe: str = "ALTA") -> dict[str, Any]:
    return _kit(
        codigo, titulo, significado, classe, "CNES",
        [
            "Ler na AIH o CNS e o CBO do profissional que aparece como executante.",
            "Conferir no CNES da competência o vínculo desse profissional com o hospital, no CBO informado.",
            "Se o CBO foi digitado errado na AIH, corrigir a AIH com o CBO do cadastro.",
            "Se o vínculo existia e faltou no CNES daquela competência, corrigir o cadastro e pedir ao gestor que "
            "importe no SIHD2 os profissionais de competências passadas antes de reapresentar.",
            "Nunca trocar por outro profissional que não executou: usar a escala e o prontuário.",
            REAPRESENTAR,
        ],
        [SISAIH, CNES_PROF, ESCALA],
        ["Espelho do CNES com o vínculo", "Escala do dia", "Registro do procedimento com o nome do executante"],
        "O FaturaSUS confere profissional e CBO no arquivo do hospital; o dado público não traz o profissional.",
        FONTE_PROFISSIONAL,
    )


def _conta(codigo: str, titulo: str, significado: str, conferir: str) -> dict[str, Any]:
    return _kit(
        codigo, titulo, significado, "ALTA", "SISAIH01",
        [conferir, "Corrigir a quantidade ou o procedimento na AIH conforme o prontuário.", SEM_MUDAR_DATAS,
         NUNCA_INVENTAR, REAPRESENTAR],
        [SISAIH, PRONTUARIO],
        ["AIH antes e depois da correção", "Trecho do prontuário que sustenta a correção"],
        "O FaturaSUS confere quantidades e compatibilidades do SIGTAP antes do envio.",
    )


def _sem_descricao(codigo: str, junto: str) -> dict[str, Any]:
    return _kit(
        codigo, f"Motivo sem descrição oficial ({codigo})",
        f"O código não está na tabela de críticas publicada. Nas AIH carregadas aparece junto de motivos de {junto}.",
        "INVESTIGAR", "SESA",
        [
            "Pedir à secretaria o significado do código e a regra do SIHD que o dispara.",
            "Separar as AIH por procedimento e tipo de leito para ver se há um padrão.",
            "Com a resposta, preencher este kit e marcar como confirmado.",
        ],
        [CRITICAS, SISAIH],
        ["Resposta da secretaria por escrito"],
    )


KITS: list[dict[str, Any]] = [
    _kit(
        "060082", "Diárias acima da capacidade instalada",
        "Na produção do mês, o SIHD soma as diárias das AIH (da internação à saída) e compara com os leitos SUS do "
        "CNES × dias do mês, sem UTI e UI, que têm conta própria. O excedente é rejeitado. Pela regra do MS, a AIH "
        "rejeitada por capacidade é cancelada e não pode ser reapresentada: não há como corrigir os leitos do CNES "
        "de competências passadas.",
        "NAO_REAPRESENTAVEL", "NENHUM",
        [
            "Não reapresentar: a regra do MS cancela a AIH rejeitada por capacidade.",
            "Contrato de gestão: usar o relatório de AIH rejeitadas do SIH para contar essas internações nas metas "
            "físicas do Plano Operativo, como o manual permite.",
            "Prevenir nos próximos meses: conferir leitos SUS em funcionamento contra o CNES e cadastrar só leito real.",
            "Hospital público com urgência 24h, referência com emergência ou maternidade de alto risco: avaliar com o "
            "gestor o cadastro de leitos reversíveis (Portaria SAS/MS 312/2002).",
            "Regular as vagas com o gestor e acompanhar a ocupação do mês antes de fechar o lote.",
        ],
        [LEITOS, CENSO, CRITICAS, CONTRATO],
        ["Relatório de AIH rejeitadas por capacidade", "Censo diário do mês", "Metas físicas do Plano Operativo"],
        "O FaturaSUS soma as diárias do lote e avisa quando passam da capacidade antes do envio.",
        FONTE_CAPACIDADE,
    ),
    _kit(
        "060084", "Diárias de UTI acima da capacidade instalada",
        "As diárias de UTI do mês passaram dos leitos de UTI habilitados no CNES × dias do mês (a UTI tem conta "
        "própria, por tipo de leito habilitado). Pela regra do MS, a AIH rejeitada por capacidade é cancelada e não "
        "pode ser reapresentada.",
        "NAO_REAPRESENTAVEL", "NENHUM",
        [
            "Não reapresentar: a regra do MS cancela a AIH rejeitada por capacidade.",
            "Contrato de gestão: contar essas internações nas metas físicas do Plano Operativo.",
            "Prevenir: conferir leitos de UTI habilitados e em funcionamento contra o CNES.",
            "Em internação longa de UTI, o manual permite encerrar a AIH e emitir nova, de comum acordo com o gestor, "
            "para apresentar no mês as diárias já usadas.",
        ],
        [LEITOS, CENSO, HABILITACOES, CRITICAS, CONTRATO],
        ["Relatório de AIH rejeitadas por capacidade", "Censo de UTI", "Portaria de habilitação dos leitos de UTI"],
        "O FaturaSUS confere diárias de UTI contra os leitos antes do envio.",
        FONTE_CAPACIDADE,
    ),
    _kit(
        "010003", "Número da AIH fora da faixa",
        "O número da AIH não está nas faixas de numeração que a secretaria autorizou para o hospital. Hoje o "
        "sistema trata como bloqueio do gestor; a proposta é tratar como recuperável de chance média, porque em "
        "geral se resolve com número dentro da faixa vigente.",
        "MEDIA", "SESA",
        [
            "Conferir as faixas de AIH autorizadas para o hospital na competência.",
            "Se o número é de faixa vencida ou de outro estabelecimento, pedir à secretaria número na faixa vigente.",
            "Se o número estava na faixa certa, pedir à secretaria a correção da faixa no SIHD.",
            REAPRESENTAR,
        ],
        [FAIXAS, SISAIH, "Autorização de internação emitida pela regulação para cada AIH"],
        ["Ofício de faixa da secretaria", "AIH com o número novo"],
        "Conferir o número de cada AIH contra a faixa autorizada antes de gerar o lote.",
    ),
    _sem_descricao("060221", "habilitação e leito"),
    _kit(
        "060120", "Procedimento exige habilitação",
        "O procedimento realizado pede, no SIGTAP, uma habilitação que o hospital não tinha no CNES. O SIHD confere "
        "a habilitação da competência da ALTA do paciente: habilitação obtida depois não recupera a AIH.",
        "MEDIA", "CNES",
        [
            "Ver no SIGTAP qual habilitação o procedimento exige.",
            "Conferir se a portaria de habilitação já valia no mês da alta do paciente.",
            "Se valia e faltou no CNES daquela competência, regularizar o cadastro com o gestor e reapresentar.",
            "Se não valia no mês da alta, não reapresentar: a AIH não volta; tratar a habilitação para os próximos meses.",
            REAPRESENTAR,
        ],
        [HABILITACOES, "CNES local: habilitações", SISAIH],
        ["Portaria de habilitação com a vigência", "Espelho do CNES com a habilitação"],
        "O FaturaSUS confere a habilitação exigida pelo procedimento antes do envio.",
        FONTE_HABILITACAO,
    ),
    _profissional(
        "060109", "Profissional sem vínculo no CNES com o CBO informado",
        "O profissional informado na AIH não tem vínculo com o hospital no CNES, no CBO que foi digitado.",
    ),
    _kit(
        "060072", "Hospital sem o serviço ou a classificação exigidos",
        "O procedimento pede um serviço/classificação que o CNES do hospital não tem como HOSPITALAR SUS, ou a AIH "
        "informou executor próprio quando o CNES tem o serviço como terceiro (ou o contrário).",
        "MEDIA", "CNES",
        [
            "Ver no SIGTAP (aba Serviço/Classificação) o que o procedimento exige.",
            "No espelho da AIH, ver qual CNES foi informado como executor: em branco é o próprio hospital.",
            "Se o CNES tem o serviço como terceiro e a AIH disse próprio (ou o contrário), corrigir o executor na AIH.",
            "Se o serviço está só como ambulatorial, marcar como hospitalar SUS no CNES e reapresentar.",
            "Se o hospital não presta o serviço, não reapresentar.",
            REAPRESENTAR,
        ],
        ["CNES local: serviços e classificações", SISAIH],
        ["Espelho do CNES com o serviço hospitalar SUS", "Espelho da AIH com o executor"],
        "O FaturaSUS confere serviço e classificação do CNES antes do envio.",
        FONTE_SERVICO,
    ),
    _leito("060022", "UTI II neonatal"),
    _kit(
        "040004", "AIH bloqueada em outro processamento",
        "A mesma AIH está bloqueada em outro processamento do SIHD. Não é erro da conta: é a situação dela lá. "
        "Hoje o sistema trata como bloqueio do gestor; a proposta é investigar, porque às vezes só falta resolver "
        "o bloqueio original.",
        "INVESTIGAR", "SESA",
        [
            "Localizar com a secretaria em qual processamento a AIH está bloqueada e por quê.",
            "Resolver o bloqueio original (auditoria, liberação, duplicidade).",
            "Não reapresentar em duplicidade enquanto o bloqueio existir.",
        ],
        [CRITICAS, SISAIH],
        ["Resposta da secretaria sobre o bloqueio"],
    ),
    _conta(
        "060150", "Diárias acima do período de internação",
        "O total de diárias lançado passa dos dias entre a internação e a alta na competência.",
        "Conferir datas de internação e alta e o total de diárias, inclusive UTI e acompanhante.",
    ),
    _profissional(
        "060147", "Profissional irregular pela Portaria SAS 134/2011",
        "O cadastro do profissional no CNES não passa nas regras da Portaria SAS 134/2011 (por exemplo, carga "
        "horária ou quantidade de vínculos incompatíveis). Proposta de chance média: só volta se o cadastro estava "
        "errado.",
        classe="MEDIA",
    ),
    _kit(
        "020081", "Internações sobrepostas do mesmo paciente",
        "O mesmo paciente (CNS) aparece em internações sobrepostas, ou com entrada até 3 dias depois da saída "
        "anterior, no mesmo ou em outro hospital. O SIHD bloqueia e só o gestor libera.",
        "MEDIA", "SESA",
        [
            "Localizar a outra AIH do mesmo CNS com a secretaria.",
            "Se o CNS foi digitado errado, corrigir o CNS e reapresentar.",
            "Se foi transferência, reinternação ou nova AIH na mesma internação, documentar e pedir a análise do gestor.",
            SEM_MUDAR_DATAS,
            "Reapresentar depois da recomendação do gestor: a AIH volta marcada como bloqueada em processamento "
            "anterior, para nova análise.",
            NUNCA_INVENTAR,
        ],
        [SISAIH, PRONTUARIO, CNS, CRITICAS],
        ["Cópia do cartão/CadSUS", "Folha de admissão, alta e transferência"],
        "O FaturaSUS confere CNS e datas do paciente no arquivo do hospital.",
        FONTE_BLOQUEIO,
    ),
    _conta(
        "060017", "Quantidade acima da permitida",
        "A quantidade lançada de um procedimento passa do máximo que o SIGTAP permite.",
        "Conferir no SIGTAP a quantidade máxima e no prontuário quantas vezes o procedimento foi feito.",
    ),
    _kit(
        "040008", "Apresentada depois do prazo: perda definitiva",
        "A AIH foi apresentada a partir do quarto mês da alta. Pela regra do MS, é rejeitada em definitivo: a alta "
        "de janeiro só pode ser apresentada em janeiro, fevereiro, março ou abril.",
        "PRAZO_VENCIDO", "NENHUM",
        [
            "Não reapresentar: a regra do MS rejeita em definitivo a AIH apresentada depois do quarto mês da alta.",
            "Registrar por que atrasou (auditoria, documento, sistema, faixa de AIH) para não repetir.",
            "Controlar o prazo de cada AIH antes de fechar o lote.",
        ],
        [SISAIH],
        [],
        "O FaturaSUS avisa quando o prazo de apresentação está vencendo.",
        FONTE_PRAZO,
    ),
    _sem_descricao("060216", "leito, profissional e prazo"),
    _leito("060028", "UTI II adulto"),
    _kit(
        "020071", "AIH bloqueada para adequar ao teto financeiro",
        "A secretaria bloqueou a AIH porque o hospital passou do teto financeiro do mês.",
        "GESTOR", "SESA",
        [
            "Conferir o teto financeiro e as metas do contrato com o gestor.",
            "Pedir à secretaria a liberação em competência com saldo.",
            "Guardar o valor bloqueado mês a mês para a revisão do contrato.",
        ],
        [CONTRATO, CRITICAS],
        ["Ofício de pedido de liberação", "Planilha de produção acima do teto"],
    ),
    _kit(
        "040006", "AIH aprovada em outro processamento",
        "A mesma AIH já foi aprovada em outro processamento: esta apresentação é duplicada. Hoje o sistema trata "
        "como bloqueio do gestor; a proposta é tratar como já recebida.",
        "JA_RECEBIDA", "NENHUM",
        [
            "Confirmar a aprovação no processamento indicado.",
            "Conferir a aprovação anterior e evitar duplicidade; recebimento exige conciliação financeira.",
            "Ver por que a AIH foi enviada duas vezes.",
        ],
        [SISAIH, CRITICAS],
        ["Relatório de AIH aprovadas do processamento anterior"],
    ),
    _conta(
        "060197", "Quantidade acima do máximo pelos dias de internação",
        "A quantidade lançada passa do máximo permitido para os dias de internação na competência (dias × 5).",
        "Conferir dias de internação na competência e a quantidade de cada procedimento.",
    ),
    _profissional(
        "060110", "Profissional vinculado não cadastrado",
        "O profissional aparece como vinculado, mas não está cadastrado no CNES do hospital na competência.",
    ),
    _profissional(
        "060065", "Profissional autônomo não cadastrado com o CBO",
        "O profissional autônomo informado não está cadastrado no hospital com o CBO da AIH.",
    ),
    _kit(
        "020069", "AIH bloqueada para auditoria do prontuário",
        "A secretaria segurou a AIH para auditar o prontuário. Hoje o sistema trata como bloqueio do gestor; a "
        "proposta é chance média, porque a auditoria aprovada libera a AIH.",
        "MEDIA", "PRONTUARIO",
        [
            "Pedir à secretaria o que a auditoria precisa ver.",
            "Enviar o prontuário completo e os documentos pedidos.",
            "Acompanhar o parecer; aprovada, a AIH é liberada no SIHD.",
        ],
        [PRONTUARIO, CRITICAS],
        ["Protocolo de envio à auditoria", "Parecer da auditoria"],
    ),
    _conta(
        "060191", "Mais de um lançamento da mesma UTI na competência",
        "A AIH tem mais de um lançamento do mesmo tipo de UTI na mesma competência.",
        "Conferir os lançamentos de UTI e juntar as diárias do mesmo tipo num lançamento só.",
    ),
    _conta(
        "060102", "Procedimento incompatível com a cirurgia relacionada",
        "O procedimento lançado não é compatível, no SIGTAP, com a cirurgia a que foi relacionado.",
        "Conferir na descrição cirúrgica qual procedimento foi feito e a compatibilidade no SIGTAP.",
    ),
    _kit(
        "020008", "AIH bloqueada por solicitação de liberação",
        "A AIH espera liberação do gestor (por exemplo, permanência maior que a prevista ou procedimento que pede "
        "autorização).",
        "GESTOR", "SESA",
        [
            "Pedir a liberação à secretaria com a justificativa clínica.",
            "Anexar o laudo e a evolução que sustentam a permanência ou o procedimento.",
            "Acompanhar a liberação no SIHD.",
        ],
        [PRONTUARIO, CRITICAS],
        ["Pedido de liberação protocolado", "Laudo médico"],
    ),
    _conta(
        "060131", "Aplicações acima do período de internação",
        "A quantidade de aplicações lançada passa do que cabe nos dias de internação.",
        "Conferir na prescrição e na checagem de enfermagem quantas aplicações foram feitas.",
    ),
    _leito("060185", "UCI neonatal convencional (UCINCo)"),
    _kit(
        "060055", "Terceiro sem o serviço ou a classificação exigidos",
        "O prestador terceiro informado na AIH não tem, no CNES, o serviço/classificação que o procedimento pede.",
        "MEDIA", "CNES",
        [
            "Identificar o terceiro informado na AIH.",
            "Conferir no CNES do terceiro o serviço e a classificação exigidos.",
            "Se o terceiro presta o serviço e faltou no cadastro, pedir a atualização do CNES dele e reapresentar.",
            "Se foi informado o terceiro errado, corrigir a AIH conforme o contrato e o prontuário.",
        ],
        ["Contratos com terceiros (laboratório, imagem, hemodinâmica)", SISAIH],
        ["Espelho do CNES do terceiro"],
        None,
        FONTE_SERVICO,
    ),
    _conta(
        "060149", "Diárias acima dos dias do mês",
        "O total de diárias lançado passa dos dias do mês da competência. Hoje o sistema trata como capacidade; a "
        "proposta é chance alta, porque é erro de lançamento na própria AIH.",
        "Conferir as diárias por competência e dividir a permanência entre as competências certas.",
    ),
    _conta(
        "060051", "Procedimento incompatível com o procedimento principal",
        "O procedimento secundário não é compatível, no SIGTAP, com o principal da AIH.",
        "Conferir no prontuário o procedimento principal e os secundários e a compatibilidade no SIGTAP.",
    ),
]


def aplicar(db) -> int:
    """Inclui os kits que ainda não existem. Devolve quantos entraram."""
    from app.models import MotiveKit

    existentes = set(db.query(MotiveKit.codigo).all())
    existentes = {c for (c,) in existentes}
    novos = [MotiveKit(**{"evidencias": [], "prevencao": None, **k}, revisao="A_CONFIRMAR")
             for k in KITS if k["codigo"] not in existentes]
    db.add_all(novos)
    db.commit()
    return len(novos)


if __name__ == "__main__":
    from sqlalchemy.orm import Session

    from app.db import engine

    with Session(engine()) as sessao:
        print(f"{aplicar(sessao)} kits incluídos")
