"""
O que pedir ao hospital ou à OSS quando o contrato é fechado, e o que o sistema
faz com cada arquivo. É a lista que aparece na Área do contrato.

Honesto sobre o que já é lido: o SISAIH01 passa pelo FaturaSUS na hora; os
demais ficam guardados, com hash, para a conferência — e dizem isso na tela.
"""
from __future__ import annotations

TIPOS: dict[str, dict[str, object]] = {
    "SISAIH01": {
        "titulo": "Arquivo SISAIH01 da competência (TXT)",
        "para_que": "É a conta como foi ou vai ser enviada. O FaturaSUS confere cada AIH contra as regras oficiais, "
                    "propõe as correções e devolve o arquivo corrigido com o relatório do que mudou.",
        "como_obter": "No SISAIH01 do hospital: exportar o movimento da competência (layout oficial 07/2026). Um arquivo "
                      "por competência; para rejeitadas, o da competência em que foram apresentadas.",
        "formatos": [".txt"],
        "leitura": "FATURASUS",
        "obrigatorio": True,
    },
    "CRITICAS_SIH": {
        "titulo": "Relatório de críticas / AIH rejeitadas do SIHD",
        "para_que": "Diz, AIH por AIH, o que a secretaria rejeitou e bloqueou — inclusive o que ainda não saiu no DATASUS.",
        "como_obter": "Relatório que a secretaria devolve após o processamento (planilha ou PDF do SIHD).",
        "formatos": [".csv", ".xlsx", ".pdf", ".txt"],
        "leitura": "GUARDADO",
        "obrigatorio": True,
    },
    "APAC_BPA": {
        "titulo": "Produção ambulatorial: APAC e BPA",
        "para_que": "O APAC barrado na consistência não aparece no dado público; só o arquivo do hospital mostra.",
        "como_obter": "Arquivo de produção exportado do sistema do hospital (APAC magnético / BPA) e o relatório de "
                      "críticas do SIA da competência.",
        "formatos": [".txt", ".csv", ".xlsx", ".pdf", ".zip"],
        "leitura": "GUARDADO",
        "obrigatorio": False,
    },
    "PROFISSIONAIS": {
        "titulo": "Médicos e profissionais: CNES local e escala",
        "para_que": "Confere CBO, vínculo e carga horária de quem executou — é o que resolve as rejeições de profissional.",
        "como_obter": "Espelho do CNES local (profissionais, vínculos, CBO) e a escala da equipe por dia.",
        "formatos": [".csv", ".xlsx", ".pdf"],
        "leitura": "GUARDADO",
        "obrigatorio": False,
    },
    "HABILITACOES": {
        "titulo": "Habilitações, leitos e contratos com terceiros",
        "para_que": "Mostra o que o CNES público ainda não reflete: portarias em andamento, leitos em funcionamento, "
                    "serviços terceirizados.",
        "como_obter": "Portarias de habilitação com vigência, censo de leitos e contratos com prestadores terceiros.",
        "formatos": [".pdf", ".xlsx", ".csv", ".zip"],
        "leitura": "GUARDADO",
        "obrigatorio": False,
    },
    "OUTRO": {
        "titulo": "Outro documento",
        "para_que": "Qualquer documento que sustente uma correção (prontuário, ofício da secretaria, contrato de gestão).",
        "como_obter": "O que o faturamento do hospital tiver.",
        "formatos": [".pdf", ".xlsx", ".csv", ".txt", ".zip", ".docx"],
        "leitura": "GUARDADO",
        "obrigatorio": False,
    },
}

LEITURAS = {
    "FATURASUS": "Conferido pelo FaturaSUS na hora do envio",
    "GUARDADO": "Guardado com hash para a conferência; a leitura automática vem depois",
}

TAMANHO_MAXIMO = 20 * 1024 * 1024
