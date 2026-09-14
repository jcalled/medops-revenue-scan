"""
De quem é o hospital e quem faz a gestão do SUS dele.

São perguntas diferentes, e o CNES responde cada uma num campo:

- **Natureza jurídica** diz quem é o dono: prefeitura, governo estadual, União,
  empresa pública, filantrópico, privado. É o que separa "hospitais das
  prefeituras" de "hospitais do governo". Hospital estadual gerido por OSS é
  do governo estadual — a OSS aparece pelo vínculo cadastrado, não aqui.
- **Esfera administrativa** (no CNES dos dados abertos) diz quem faz a gestão
  do contrato SUS: municipal ou estadual. Um hospital filantrópico em município
  com gestão plena aparece como MUNICIPAL.

Os códigos de natureza jurídica seguem a tabela da CONCLA/IBGE: os três
primeiros dígitos dizem a esfera (101 órgão federal, 102 estadual, 103
municipal, 124 município...).
"""
from __future__ import annotations

NATUREZAS: dict[str, str] = {
    "MUNICIPAL": "Prefeitura (público municipal)",
    "ESTADUAL": "Governo estadual",
    "FEDERAL": "Governo federal",
    "OUTRO_PUBLICO": "Outro órgão ou consórcio público",
    "EMPRESA_PUBLICA": "Empresa pública ou de economia mista",
    "FILANTROPICO": "Filantrópico / sem fins lucrativos",
    "PRIVADO": "Privado com fins lucrativos",
    "NAO_INFORMADA": "Natureza não informada",
}

GESTOES: dict[str, str] = {
    "MUNICIPAL": "Gestão municipal",
    "ESTADUAL": "Gestão estadual",
    "DUPLA": "Gestão dupla",
    "FEDERAL": "Gestão federal",
}

ORDEM_PORTES = ("até 50 leitos", "51 a 150 leitos", "151 a 300 leitos", "mais de 300 leitos", "sem leitos no CNES")

_FEDERAL = {"101", "104", "107", "110", "113", "116", "120", "125", "128", "131"}
_ESTADUAL = {"102", "105", "108", "111", "114", "117", "121", "123", "126", "129"}
_MUNICIPAL = {"103", "106", "112", "115", "118", "122", "124", "127", "130"}
_EMPRESA_PUBLICA = {"201", "203"}


def grupo_natureza(codigo: str | None) -> str:
    texto = (codigo or "").strip()
    if len(texto) < 3 or not texto.isdigit():
        return "NAO_INFORMADA"
    prefixo = texto[:3]
    if prefixo in _MUNICIPAL:
        return "MUNICIPAL"
    if prefixo in _ESTADUAL:
        return "ESTADUAL"
    if prefixo in _FEDERAL:
        return "FEDERAL"
    if texto[0] == "1":
        return "OUTRO_PUBLICO"
    if prefixo in _EMPRESA_PUBLICA:
        return "EMPRESA_PUBLICA"
    if texto[0] in "24":
        return "PRIVADO"
    if texto[0] == "3":
        return "FILANTROPICO"
    return "NAO_INFORMADA"


def gestao(esfera: str | None) -> str | None:
    valor = (esfera or "").strip().upper()
    return valor if valor in GESTOES else None
