"""Planos explicáveis sobre evidência pública; nunca altera a conta de origem."""
from datetime import date, datetime, timezone
from typing import Any

from app.domain.recuperacao import prazo_estimado

from app.domain.regras_correcao_ampliadas import REGRAS_AMPLIADAS

VERSAO = "plano-publico-v2"
FONTE = "https://saude.prefeitura.rio/wp-content/uploads/sites/47/2025/07/Totais-de-erros-CNES-062025.pdf"
# As descrições dos motivos são públicas. A conduta abaixo é uma proposta
# operacional MedOps, condicionada à verificação da conta e da competência.
REGRAS = {
    "060082": {
        "titulo": "Diárias acima da capacidade instalada", "campo": "Nenhum: a AIH foi cancelada", "onde": "Contrato de gestão e prevenção",
        "problema": "O processamento apontou quantidade de diárias superior à capacidade instalada (leitos SUS gerais × dias do mês).",
        "depois": "Não há estado corrigido: pela regra do MS a AIH é cancelada e não pode ser reapresentada.",
        "acao": "Não reapresentar. Contar a internação nas metas físicas do contrato de gestão e prevenir nos próximos meses: leitos SUS em funcionamento no CNES, leitos reversíveis onde a regra deixa e regulação de vagas.",
        "porque": "Não há como corrigir os leitos do CNES de competências anteriores; por isso o manual cancela a AIH.",
        "documentos": ["Relatório de AIH rejeitadas do SIH", "Metas físicas do Plano Operativo", "Censo diário do mês"],
        "fonte": "http://sihd.datasus.gov.br/documentos/documentos_sihd2.php",
        "reapresentavel": False,
    },
    "060084": {
        "titulo": "Diárias de UTI acima da capacidade instalada", "campo": "Nenhum: a AIH foi cancelada", "onde": "Contrato de gestão e prevenção",
        "problema": "O processamento apontou diárias de UTI acima dos leitos de UTI habilitados × dias do mês.",
        "depois": "Não há estado corrigido: pela regra do MS a AIH é cancelada e não pode ser reapresentada.",
        "acao": "Não reapresentar. Contar nas metas do contrato de gestão; em UTI longa, o manual permite encerrar a AIH e emitir nova, com o gestor, para apresentar no mês as diárias já usadas.",
        "porque": "Não há como corrigir os leitos do CNES de competências anteriores; por isso o manual cancela a AIH.",
        "documentos": ["Relatório de AIH rejeitadas do SIH", "Censo de UTI", "Metas físicas do Plano Operativo"],
        "fonte": "http://sihd.datasus.gov.br/documentos/documentos_sihd2.php",
        "reapresentavel": False,
    },
    "060109": {
        "titulo": "Profissional sem vínculo no CBO informado", "campo": "CNS/CBO do executante e vínculo CNES", "onde": "Conta da AIH ou cadastro CNES",
        "problema": "O processamento não reconheceu o vínculo do profissional com o CNES no CBO informado.",
        "depois": "Executante real, CBO correto e vínculo válido para o atendimento e a competência.",
        "acao": "Conferir quem executou o atendimento. Se o CBO foi digitado incorretamente, corrigir conforme o registro; se o vínculo foi omitido, verificar regularização com o responsável pelo CNES.",
        "porque": "A regra exige coerência entre executante, CBO e vínculo. A proposta trata essa coerência; não autoriza substituir o profissional por outro que não participou do atendimento.",
        "documentos": ["CNS e CBO enviados na AIH", "Registro do executante e escala", "Vínculo CNES na competência"],
    },
    "060120": {
        "titulo": "Procedimento exige habilitação", "campo": "Procedimento e habilitação do estabelecimento", "onde": "SIGTAP, CNES e gestor",
        "problema": "O processamento apontou exigência de habilitação para o procedimento realizado.",
        "depois": "Habilitação exigida conferida e vigente, com cadastro regular para a competência.",
        "acao": "Consultar a exigência do procedimento no SIGTAP e a portaria vigente. Se a habilitação existia e foi omitida, solicitar regularização. Se não existia, encaminhar ao gestor; não trocar o procedimento para contornar a exigência.",
        "porque": "Regularizar uma omissão comprovada pode resolver a incompatibilidade cadastral. Uma habilitação concedida depois não comprova elegibilidade no atendimento anterior.",
        "documentos": ["Procedimento da conta original", "SIGTAP da competência", "Portaria e CNES de habilitação vigentes"],
    },
}

REGRAS.update(REGRAS_AMPLIADAS)

def montar_plano(historico: dict[str, Any], referencia: str) -> dict[str, Any]:
    rejeicoes = [e for e in historico["eventos"] if e["situacao"] == "REJEITADA"]
    origem = max(rejeicoes, key=lambda e: e["competencia"])
    aprovacoes = [e for e in historico["eventos"] if e["situacao"] == "APROVADA" and e["competencia"] > origem["competencia"]]
    aprovada = min(aprovacoes, key=lambda e: e["competencia"]) if aprovacoes else None
    alta = origem["campos"].get("dt_saida")
    prazo = prazo_estimado(date.fromisoformat(alta)) if alta else None
    motivos = sorted(set(origem["motivos"]))
    planos = []
    for codigo in motivos:
        regra = REGRAS.get(codigo)
        antes = "Valor do campo não disponível no recorte público; verificar a conta e o cadastro de origem."
        if codigo == "060120" and origem["campos"].get("procedimento"):
            antes = f"Procedimento publicado: {origem['campos']['procedimento']}. A habilitação aplicável ainda precisa ser conferida no SIGTAP, portaria e CNES da competência."
        planos.append({
            "codigo": codigo, "coberto": regra is not None,
            "titulo": regra["titulo"] if regra else "Motivo ainda sem plano de correção",
            "problema": regra["problema"] if regra else f"O ER publicou o motivo {codigo}; esta versão ainda não interpreta sua correção.",
            "campo": regra["campo"] if regra else "A identificar no relatório de críticas",
            "onde": regra["onde"] if regra else "Faturamento e gestor",
            "antes": antes,
            "depois": regra["depois"] if regra else "A definir após identificar a regra do motivo.",
            "valor_proposto": None,
            "acao": regra["acao"] if regra else "Obter a descrição oficial e registrar uma conduta revisada antes de propor alterações.",
            "porque": regra["porque"] if regra else "Sem regra validada não há justificativa para modificar a conta.",
            "documentos": regra["documentos"] if regra else ["Relatório de críticas do gestor", "Conta original"],
            "fonte_descricao": regra.get("fonte", FONTE) if regra else None,
            "natureza": "PROPOSTA_CONDICIONAL" if regra else "SEM_REGRA",
            "reapresentavel": regra.get("reapresentavel", True) if regra else None,
        })
    pendencias = ["Conferir a conta original e documentar o valor correto de cada campo.",
                  "Validar todos os motivos e as regras da competência após as alterações.",
                  "Confirmar autorização e calendário de reapresentação com o gestor."]
    if not motivos or any(not p["coberto"] for p in planos):
        pendencias.insert(0, "Há motivo sem plano ou ausência de motivos no ER; a análise não cobre toda a rejeição.")
    if not prazo:
        pendencias.insert(0, "Data de alta indisponível; janela de reapresentação não calculada.")
    elif prazo < referencia:
        pendencias.insert(0, "Janela estimada encerrada; verificar enquadramento com o gestor antes de reapresentar.")
    cancelada = any(p["reapresentavel"] is False for p in planos)
    if cancelada:
        pendencias.insert(0, "Regra do MS: AIH rejeitada por capacidade instalada é cancelada e não pode ser reapresentada "
                             "(Manual Técnico Operacional do SIH, jan/2017, item 59.1). Conte a internação nas metas do "
                             "contrato de gestão e trabalhe a prevenção.")
    if aprovada:
        pendencias.insert(0, "Já há aprovação posterior localizada: conferir o resultado e evitar reapresentação duplicada. Este plano serve para estudar o caso histórico.")
    return {
        "versao": VERSAO, "gerado_em": datetime.now(timezone.utc).isoformat(),
        "cnes": historico["cnes"], "n_aih": historico["n_aih"], "referencia": referencia,
        "origem": origem, "aprovacao_posterior": aprovada, "prazo_estimado": prazo,
        "situacao": ("APROVACAO_POSTERIOR_LOCALIZADA" if aprovada
                     else "NAO_REAPRESENTAVEL" if cancelada else "AGUARDANDO_VALIDACAO"),
        "motivos_cobertos": sum(p["coberto"] for p in planos), "motivos_total": len(motivos),
        "planos": planos, "pendencias": pendencias,
        "alteracoes_aplicadas": 0, "pronta_para_reapresentar": False,
        "leitura": "O motor propõe ações condicionais por motivo. Nenhuma conta foi alterada ou validada para envio. Aprovação posterior é evidência histórica, não comprovação de que uma mudança publicada causou a aprovação.",
        "historico": historico,
    }
