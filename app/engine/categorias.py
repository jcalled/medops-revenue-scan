"""
Motivos de rejeição do SIH agrupados pelo que o hospital pode fazer.

A lista vem da tabela oficial de motivos (TAB_SIH, tabela 0027) conferida
contra os motivos que mais rejeitaram AIH no Ceará em mai–jul/2026. Código fora
da lista cai em OUTROS: aparece no scan, com confiança baixa, em vez de sumir.
Código sem descrição oficial (060221, por exemplo) também fica em OUTROS —
classificá-lo seria adivinhar.

Uma AIH com vários motivos entra numa categoria só. Bloqueios, motivos
desconhecidos e prazo têm prioridade: corrigir outro motivo não os resolve.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Categoria:
    codigo: str
    nome: str
    opportunity_type: str
    # Quanto a categoria depende do hospital, 0 a 100. É a confiança de partida
    # da oportunidade; amostra pequena e poucos semelhantes a reduzem.
    confianca: int
    acao: str
    motivos: frozenset[str]


# Em ordem de prioridade: a primeira que casar com um motivo da AIH é a dela.
CATEGORIAS: tuple[Categoria, ...] = (
    Categoria(
        "LEITO_CNES", "Leito cobrado sem cadastro no CNES", "CNES_ENABLEMENT_RISK", 85,
        "Conferir no CNES os leitos de UTI, UCI e da especialidade cobrados; cadastrar o leito existente ou "
        "corrigir o tipo de leito informado antes de reapresentar.",
        frozenset({"060022", "060028", "060185", "060186", "050008"}),
    ),
    Categoria(
        "HABILITACAO_SERVICO", "Procedimento sem habilitação ou serviço no CNES", "CNES_ENABLEMENT_RISK", 85,
        "Conferir habilitações e serviços/classificações do CNES para os procedimentos rejeitados; solicitar a "
        "habilitação ou deixar de faturar o procedimento por este estabelecimento.",
        frozenset({"060120", "050098", "060072", "060055"}),
    ),
    Categoria(
        "CAPACIDADE", "Diárias acima da capacidade instalada", "AIH_REJECTION", 85,
        "Conferir os leitos SUS no CNES e a regra de capacidade do gestor para a reapresentação, "
        "antes do fechamento do lote.",
        frozenset({"060082", "060084", "060083", "060188", "060189", "060149"}),
    ),
    Categoria(
        "PRAZO", "Apresentação fora do prazo", "AIH_REJECTION", 80,
        "Controlar a apresentação de cada AIH dentro de quatro meses da alta e a competência de execução.",
        frozenset({"040008", "060146"}),
    ),
    Categoria(
        "REGRAS_SIGTAP", "Incompatibilidade com as regras do SIGTAP", "AIH_REJECTION", 75,
        "Conferir antes do envio quantidades, compatibilidades entre procedimentos, OPM e permanência pelas "
        "regras do SIGTAP.",
        frozenset({"060017", "060197", "060051", "060102", "060150", "060131", "050142", "060205", "080006",
                   "060108", "050096", "060201", "060204", "060195", "060191", "060178", "060206", "060101",
                   "060193", "060132", "050006", "060171", "050116"}),
    ),
    Categoria(
        "PROFISSIONAL", "Profissional sem vínculo ou CBO no CNES", "PROFESSIONAL_CBO_RISK", 70,
        "Atualizar no CNES o vínculo e o CBO dos profissionais que aparecem nas AIH antes do fechamento.",
        frozenset({"060109", "060110", "060147", "060065", "060074", "060057", "060167"}),
    ),
    Categoria(
        "PACIENTE", "Dados do paciente (CNS, internações sobrepostas)", "AIH_REJECTION", 60,
        "Conferir CNS do paciente e datas de internação e alta antes do envio.",
        frozenset({"020081", "020077", "050057"}),
    ),
    Categoria(
        "OUTROS", "Outros motivos", "AIH_REJECTION", 40,
        "Revisar estes motivos com o faturamento; parte deles não tem descrição na tabela oficial.",
        frozenset(),
    ),
    Categoria(
        "ADMINISTRATIVO", "Bloqueio administrativo do gestor", "AIH_REJECTION", 25,
        "Tratar com o gestor (faixa de numeração, teto financeiro, auditoria, bloqueios); em geral fora do "
        "alcance do faturamento do hospital.",
        frozenset({"010003", "020071", "040004", "040006", "020069", "020008", "040009", "040003", "020075"}),
    ),
)

POR_CODIGO: dict[str, Categoria] = {c.codigo: c for c in CATEGORIAS}
OUTROS = POR_CODIGO["OUTROS"]
# Rejeição registrada, mas não corrigível pelo faturamento (bloqueio do gestor)
# ou sem regra de correção (motivo desconhecido). Não entra no valor confirmado
# nem na lista de AIH a recuperar: o que se cobra tem que ser provável AIH a AIH.
FORA_DA_RECUPERACAO = frozenset({"ADMINISTRATIVO", "OUTROS"})
_ORDEM = {c.codigo: i for i, c in enumerate(CATEGORIAS)}
_POR_MOTIVO = {motivo: c for c in CATEGORIAS for motivo in c.motivos}


def categorizar(motivos: Iterable[str]) -> Categoria:
    """Prioriza impedimentos; sem eles, escolhe a categoria operacional."""
    encontradas = {_POR_MOTIVO.get(m, OUTROS) for m in motivos}
    # Um bloqueio ou motivo desconhecido não desaparece ao corrigir outro motivo.
    for codigo in ("ADMINISTRATIVO", "OUTROS", "PRAZO"):
        if POR_CODIGO[codigo] in encontradas:
            return POR_CODIGO[codigo]
    return min(encontradas, key=lambda c: _ORDEM[c.codigo]) if encontradas else OUTROS
