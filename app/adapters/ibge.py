"""Códigos IBGE das UFs, como o DATASUS usa nos nomes de arquivo e nas APIs."""

CODIGO_UF: dict[str, str] = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
    "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27", "SE": "28", "BA": "29",
    "MG": "31", "ES": "32", "RJ": "33", "SP": "35",
    "PR": "41", "SC": "42", "RS": "43",
    "MS": "50", "MT": "51", "GO": "52", "DF": "53",
}
UF_POR_CODIGO: dict[str, str] = {codigo: uf for uf, codigo in CODIGO_UF.items()}


URL_MUNICIPIOS = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"


def municipios_ibge(*, transport=None, timeout: float = 60.0) -> list[dict[str, str]]:
    """Os municípios do país numa chamada. O código do CNES é o do IBGE sem o dígito verificador."""
    import httpx

    with httpx.Client(transport=transport, timeout=timeout) as cliente:
        resposta = cliente.get(URL_MUNICIPIOS)
        resposta.raise_for_status()
        dados = resposta.json()
    return [
        {"codigo": str(m["municipio-id"]), "codigo_cnes": str(m["municipio-id"])[:6],
         "nome": m["municipio-nome"], "uf": m["UF-sigla"]}
        for m in dados
    ]


def validar_uf(uf: str) -> str:
    sigla = (uf or "").strip().upper()
    if sigla not in CODIGO_UF:
        raise ValueError(f"UF desconhecida: {uf!r}")
    return sigla
