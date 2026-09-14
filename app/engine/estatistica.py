"""Quartis e percentil sem dependência externa: o grupo de semelhantes tem no máximo 15 hospitais."""
from __future__ import annotations

from collections.abc import Iterable


def quantil(valores: Iterable[float | None], q: float) -> float | None:
    """Quantil com interpolação linear (o mesmo método padrão de planilhas)."""
    ordenados = sorted(v for v in valores if v is not None)
    if not ordenados:
        return None
    posicao = (len(ordenados) - 1) * q
    base = int(posicao)
    if base + 1 >= len(ordenados):
        return ordenados[base]
    return ordenados[base] + (ordenados[base + 1] - ordenados[base]) * (posicao - base)


def percentil(valor: float | None, valores: Iterable[float | None]) -> float | None:
    """Posição do valor entre os demais, 0 a 100. Empate conta meio."""
    lista = [v for v in valores if v is not None]
    if valor is None or not lista:
        return None
    abaixo = sum(1 for v in lista if v < valor)
    iguais = sum(1 for v in lista if v == valor)
    return round(100 * (abaixo + 0.5 * iguais) / len(lista), 1)
