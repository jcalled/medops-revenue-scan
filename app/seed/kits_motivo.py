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

REAPRESENTAR = "Reapresentar no SISAIH01 dentro de quatro meses da alta."
NUNCA_INVENTAR = "Corrigir só o que o prontuário sustenta: nunca mudar o que foi feito no paciente para a conta passar."


def _kit(codigo: str, titulo: str, significado: str, classe: str, onde: str, passos: list[str],
         dados: list[str], evidencias: list[str], prevencao: str | None = None) -> dict[str, Any]:
    return {
        "codigo": codigo, "titulo": titulo, "significado": significado, "classe": classe, "onde_corrigir": onde,
        "passos": passos, "dados_do_hospital": dados, "evidencias": evidencias, "prevencao": prevencao,
        "fonte": FONTE,
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
            "Se o vínculo existe e faltou no CNES, atualizar o CNES; se o CBO foi digitado errado, corrigir a AIH.",
            "Nunca trocar por outro profissional que não executou: usar a escala e o prontuário.",
            REAPRESENTAR,
        ],
        [SISAIH, CNES_PROF, ESCALA],
        ["Espelho do CNES com o vínculo", "Escala do dia", "Registro do procedimento com o nome do executante"],
        "O FaturaSUS confere profissional e CBO no arquivo do hospital; o dado público não traz o profissional.",
    )


def _conta(codigo: str, titulo: str, significado: str, conferir: str) -> dict[str, Any]:
    return _kit(
        codigo, titulo, significado, "ALTA", "SISAIH01",
        [conferir, "Corrigir a quantidade ou o procedimento na AIH conforme o prontuário.", NUNCA_INVENTAR, REAPRESENTAR],
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
        "O SIH soma as diárias das AIH do hospital na competência e compara com o que os leitos SUS do CNES "
        "comportam (leitos × dias). As AIH que passam do limite são rejeitadas.",
        "INCERTA", "CNES",
        [
            "Comparar os leitos SUS do CNES na competência com os leitos realmente em funcionamento.",
            "Se há leito SUS funcionando fora do CNES, atualizar o CNES antes de reapresentar.",
            "Conferir no censo diário se as diárias informadas batem com a ocupação; corrigir diárias lançadas a mais.",
            "Pedir à secretaria o relatório de capacidade do SIHD: quanto sobrou em cada mês.",
            "Reapresentar as AIH em competência com folga de capacidade, dentro de quatro meses da alta.",
        ],
        [LEITOS, CENSO, SISAIH, CRITICAS],
        ["Espelho do CNES antes e depois", "Censo diário do mês", "Mês em que cada AIH foi reapresentada"],
        "O FaturaSUS soma as diárias do lote e avisa quando passam da capacidade antes do envio.",
    ),
    _kit(
        "060084", "Diárias de UTI acima da capacidade instalada",
        "As diárias de UTI da competência passaram do que os leitos de UTI SUS do CNES comportam.",
        "INCERTA", "CNES",
        [
            "Conferir leitos de UTI SUS cadastrados, habilitados e em funcionamento na competência.",
            "Conferir no censo de UTI se as diárias informadas batem com a ocupação.",
            "Atualizar o CNES se há leito de UTI habilitado funcionando fora do cadastro.",
            "Reapresentar em competência com folga de capacidade de UTI, dentro de quatro meses da alta.",
        ],
        [LEITOS, CENSO, HABILITACOES, SISAIH, CRITICAS],
        ["Espelho do CNES", "Censo de UTI", "Portaria de habilitação dos leitos de UTI"],
        "O FaturaSUS confere diárias de UTI contra os leitos antes do envio.",
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
        "O procedimento realizado pede, no SIGTAP, uma habilitação que o hospital não tem no CNES da competência.",
        "MEDIA", "CNES",
        [
            "Ver no SIGTAP qual habilitação o procedimento exige.",
            "Conferir se o hospital tem essa habilitação em portaria vigente na competência da AIH.",
            "Se tem portaria e faltou no CNES, atualizar o CNES e reapresentar.",
            "Se não tem habilitação, não reapresentar: tratar o pedido com a secretaria ou deixar de cobrar o "
            "procedimento por este estabelecimento.",
        ],
        [HABILITACOES, "CNES local: habilitações", SISAIH],
        ["Portaria de habilitação", "Espelho do CNES com a habilitação"],
        "O FaturaSUS confere a habilitação exigida pelo procedimento antes do envio.",
    ),
    _profissional(
        "060109", "Profissional sem vínculo no CNES com o CBO informado",
        "O profissional informado na AIH não tem vínculo com o hospital no CNES, no CBO que foi digitado.",
    ),
    _kit(
        "060072", "Hospital sem o serviço ou a classificação exigidos",
        "O procedimento pede um serviço/classificação do CNES que o hospital não tem cadastrado na competência.",
        "MEDIA", "CNES",
        [
            "Ver no SIGTAP o serviço e a classificação que o procedimento exige.",
            "Conferir se o hospital presta o serviço e se ele está no CNES da competência.",
            "Se presta e faltou no cadastro, atualizar o CNES e reapresentar.",
            "Se não presta, não reapresentar.",
        ],
        ["CNES local: serviços e classificações", SISAIH],
        ["Espelho do CNES com o serviço"],
        "O FaturaSUS confere serviço e classificação do CNES antes do envio.",
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
        "O mesmo paciente (CNS) aparece internado em períodos que se sobrepõem, nesta ou em outra AIH, às vezes "
        "de outro hospital.",
        "ALTA", "SISAIH01",
        [
            "Localizar a outra AIH do mesmo CNS com a secretaria.",
            "Conferir no prontuário as datas de internação e alta e o CNS do paciente.",
            "Se foi erro de data ou de CNS, corrigir e reapresentar.",
            "Se foi transferência ou reinternação, lançar conforme a regra e documentar.",
            NUNCA_INVENTAR,
        ],
        [SISAIH, PRONTUARIO, CNS, CRITICAS],
        ["Cópia do cartão/CadSUS", "Folha de admissão e alta"],
        "O FaturaSUS confere CNS e datas do paciente no arquivo do hospital.",
    ),
    _conta(
        "060017", "Quantidade acima da permitida",
        "A quantidade lançada de um procedimento passa do máximo que o SIGTAP permite.",
        "Conferir no SIGTAP a quantidade máxima e no prontuário quantas vezes o procedimento foi feito.",
    ),
    _kit(
        "040008", "Fora do prazo de quatro meses",
        "A AIH foi apresentada mais de quatro meses depois da alta.",
        "PRAZO_VENCIDO", "NENHUM",
        [
            "Não há reapresentação pela via normal.",
            "Registrar por que atrasou (auditoria, documento, sistema) para não repetir.",
        ],
        [SISAIH],
        [],
        "O FaturaSUS avisa quando o prazo de apresentação está vencendo.",
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
            "Não reapresentar: o valor já entrou.",
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
