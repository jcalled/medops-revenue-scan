"""
Organizações e os hospitais que elas gerem, com a fonte de cada vínculo.

Hospital estadual gerido por OSS aparece no CNES com o CNPJ da secretaria de
saúde, não da OSS. O vínculo vem da própria organização (site, transparência
da secretaria) conferido pelo nome fantasia no CNES — e é por isso que cada
linha tem fonte, situação e data de verificação.

Qualquer OSS do país entra por planilha, sem mexer em código:

    python -m app.seed.organizacoes                        # organizações conferidas (ISGH)
    python -m app.seed.organizacoes --csv oss.csv          # uma linha por hospital
    python -m app.seed.organizacoes --buscar "REGIONAL" --uf GO   # achar o CNES pelo nome

Colunas do CSV (vírgula ou ponto e vírgula):
    sigla, nome, cnpj, uf, site, fonte, cnes, sigla_unidade, situacao, verificado_em
`situacao` é CONFIRMADO ou A_CONFIRMAR (padrão); `verificado_em` é AAAA-MM-DD.
"""
from __future__ import annotations

import argparse
import csv
import re
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Establishment, ManagementOrganization, OrganizationEstablishment

SITUACOES = {"CONFIRMADO", "A_CONFIRMAR"}


class _PontoEVirgula(csv.excel):
    """Planilha exportada pelo Excel em português, quando o formato não dá para adivinhar."""

    delimiter = ";"

ORGANIZACOES: list[dict[str, Any]] = [
    {
        "sigla": "ISGH",
        "nome": "Instituto de Saúde e Gestão Hospitalar",
        "cnpj": "05268526000170",
        "uf": "CE",
        "site": "https://www.isgh.org.br/",
        "fonte": "https://www.isgh.org.br/quem-somos",
        "unidades": {
            "2785900": "HGWA",   # Hospital Geral Dr. Waldemar Alcântara
            "6779522": "HRC",    # Hospital Regional do Cariri
            "6848710": "HRN",    # Hospital Regional Norte
            "7061021": "HRSC",   # Hospital Regional do Sertão Central
            "9672427": "HRVJ",   # Hospital Regional Vale do Jaguaribe
            "0086673": "HELV",   # Hospital Estadual Leonardo da Vinci
            "0153087": "HRI",    # Hospital Regional de Itapipoca
            "4963938": "HUC",    # Hospital Universitário do Ceará
        },
        "fonte_unidades": "isgh.org.br/quem-somos, conferido pelo nome fantasia no CNES de jul/2026",
        "verificado_em": date(2026, 9, 14),
        "situacao": "CONFIRMADO",
    },
]


def aplicar(db: Session, organizacoes: list[dict[str, Any]] = ORGANIZACOES) -> dict[str, int]:
    """Cria ou atualiza. Rodar de novo não duplica; hospital fora da lista não é apagado."""
    contagem = {"organizacoes": 0, "unidades": 0}
    for dados in organizacoes:
        org = db.execute(
            select(ManagementOrganization).where(ManagementOrganization.sigla == dados["sigla"])
        ).scalar_one_or_none()
        if org is None:
            org = ManagementOrganization(sigla=dados["sigla"], nome=dados["nome"])
            db.add(org)
        org.nome, org.cnpj, org.uf, org.site, org.fonte = (
            dados["nome"], dados.get("cnpj"), dados.get("uf"), dados.get("site"), dados.get("fonte"))
        existentes = {u.cnes: u for u in org.unidades}
        situacoes = dados.get("situacoes", {})
        for cnes, sigla in dados["unidades"].items():
            unidade = existentes.get(cnes) or OrganizationEstablishment(cnes=cnes)
            if cnes not in existentes:
                org.unidades.append(unidade)
            unidade.sigla = sigla
            unidade.situacao = situacoes.get(cnes, dados.get("situacao", "A_CONFIRMAR"))
            unidade.fonte = dados.get("fonte_unidades")
            unidade.verificado_em = dados.get("verificado_em")
            contagem["unidades"] += 1
        contagem["organizacoes"] += 1
    db.commit()
    return contagem


def ler_csv(caminho: Path) -> list[dict[str, Any]]:
    """Organizações de uma planilha com uma linha por hospital (ou só a organização, sem CNES)."""
    with open(caminho, encoding="utf-8-sig", newline="") as arquivo:
        amostra = arquivo.read(4096)
        arquivo.seek(0)
        try:
            dialeto: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(amostra, delimiters=";,")
        except csv.Error:
            dialeto = _PontoEVirgula
        organizacoes: dict[str, dict[str, Any]] = {}
        for numero, bruta in enumerate(csv.DictReader(arquivo, dialect=dialeto), start=2):
            linha = {(k or "").strip().lower(): (v or "").strip() for k, v in bruta.items()}
            sigla = linha.get("sigla", "").upper()
            if not sigla or not linha.get("nome"):
                raise ValueError(f"Linha {numero}: sigla e nome são obrigatórios")
            org = organizacoes.setdefault(sigla, {
                "sigla": sigla,
                "nome": linha["nome"],
                "cnpj": re.sub(r"\D", "", linha.get("cnpj", "")) or None,
                "uf": linha.get("uf", "").upper() or None,
                "site": linha.get("site") or None,
                "fonte": linha.get("fonte") or None,
                "fonte_unidades": linha.get("fonte") or None,
                "unidades": {},
                "situacoes": {},
                "verificado_em": None,
            })
            cnes = re.sub(r"\D", "", linha.get("cnes", ""))
            if not cnes:
                continue
            if len(cnes) > 7:
                raise ValueError(f"Linha {numero}: CNES inválido {linha['cnes']!r}")
            situacao = (linha.get("situacao") or "A_CONFIRMAR").upper()
            if situacao not in SITUACOES:
                raise ValueError(f"Linha {numero}: situação {situacao!r}; use CONFIRMADO ou A_CONFIRMAR")
            org["unidades"][cnes.zfill(7)] = linha.get("sigla_unidade") or None
            org["situacoes"][cnes.zfill(7)] = situacao
            if linha.get("verificado_em"):
                org["verificado_em"] = date.fromisoformat(linha["verificado_em"])
    return list(organizacoes.values())


def buscar_estabelecimentos(db: Session, termo: str, uf: str | None = None, limite: int = 30) -> list[dict[str, Any]]:
    """
    Hospitais já carregados cujo nome contém o termo.

    É como se acha o CNES de uma unidade que a OSS lista só pelo nome. Só
    aparecem estabelecimentos com AIH no SIH carregado — hospital, não UBS.
    """
    padrao = f"%{termo.strip().lower()}%"
    consulta = select(Establishment).where(or_(
        func.lower(Establishment.nome_fantasia).like(padrao), func.lower(Establishment.razao_social).like(padrao),
    ))
    if uf:
        consulta = consulta.where(Establishment.uf == uf.strip().upper())
    consulta = consulta.order_by(Establishment.uf, Establishment.nome_fantasia).limit(limite)
    return [
        {"cnes": e.cnes, "nome_fantasia": e.nome_fantasia, "razao_social": e.razao_social, "uf": e.uf,
         "codigo_municipio": e.codigo_municipio, "cnpj_entidade": e.cnpj_entidade}
        for e in db.execute(consulta).scalars()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Organizações gestoras e seus hospitais.")
    parser.add_argument("--csv", type=Path, help="Planilha com uma linha por hospital")
    parser.add_argument("--buscar", help="Procura CNES pelo nome do hospital")
    parser.add_argument("--uf", help="Limita a busca a uma UF")
    args = parser.parse_args(argv)

    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    with sessionmaker(bind=engine())() as db:
        if args.buscar:
            for e in buscar_estabelecimentos(db, args.buscar, args.uf):
                print(f"{e['cnes']}  {e['uf'] or '--'}  {e['nome_fantasia'] or ''}  ({e['razao_social'] or ''})")
            return 0
        print(aplicar(db, ler_csv(args.csv) if args.csv else ORGANIZACOES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
