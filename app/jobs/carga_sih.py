"""
Carga do SIH/SUS por UF e competência.

Uma competência entra inteira ou não entra: aprovadas (RD), rejeitadas (RJ) e
motivos (ER) do mesmo processamento são gravados na mesma transação.
Recarregar uma competência substitui a anterior — o DATASUS republica arquivos.

Cada AIH conta uma vez por arquivo: o DATASUS repete algumas linhas no mesmo
processamento, e contar a repetição inflaria a rejeição. É a mesma regra do
painel do Ceará validado contra o SIH.

Uso:
    python -m app.jobs.carga_sih --uf CE --quantidade 3
    python -m app.jobs.carga_sih --uf CE --competencias 202605,202606,202607 --pasta /dados/sih
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.adapters import datasus
from app.adapters.base import DataSourceAdapter, OrigemArquivo
from app.adapters.cnes_api import CnesDadosAbertos
from app.adapters.ibge import CODIGO_UF, validar_uf
from app.adapters.sih import TIPOS, SihAdapter
from app.adapters.sih_erros import ler_codigos_de_erro, obter_tab_sih
from app.models import (
    DataLoad, Establishment, SihApprovedAih, SihErrorCode, SihHospitalMonth, SihHospitalProcedureMonth, SihRejection,
    SihRejectionReason,
)

logger = logging.getLogger(__name__)
_LOTE = 5_000


@dataclass(frozen=True)
class ResultadoCarga:
    uf: str
    competencia: str
    aprovadas: int
    rejeitadas: int
    motivos: int
    hospitais: int


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def adapters_padrao(pasta_local: Path | None = None) -> dict[str, DataSourceAdapter]:
    return {tipo: SihAdapter(tipo, pasta_local=pasta_local) for tipo in TIPOS}


def _inserir(db: Session, modelo: type, linhas: list[dict[str, Any]]) -> None:
    for inicio in range(0, len(linhas), _LOTE):
        db.execute(insert(modelo), linhas[inicio:inicio + _LOTE])


def carregar_competencia(db: Session, uf: str, competencia: str, adapters: dict[str, DataSourceAdapter],
                         pasta_trabalho: Path) -> ResultadoCarga:
    uf = validar_uf(uf)
    cargas = {
        tipo: DataLoad(fonte=adapters[tipo].fonte, uf=uf, competencia=competencia,
                       origem=adapters[tipo].origem.value, status="RUNNING")
        for tipo in TIPOS
    }
    db.add_all(cargas.values())
    db.commit()

    baixados: list[Path] = []
    try:
        caminhos: dict[str, Path] = {}
        for tipo in TIPOS:
            caminho = adapters[tipo].obter(uf, competencia, pasta_trabalho)
            if adapters[tipo].origem == OrigemArquivo.DOWNLOAD:
                baixados.append(caminho)
            caminhos[tipo] = caminho
            cargas[tipo].arquivo = caminho.name
            cargas[tipo].checksum = datasus.sha256(caminho)

        # A última linha de cada AIH no arquivo vence.
        aprovadas = {linha["n_aih"]: linha for linha in adapters["RD"].ler(caminhos["RD"])}
        rejeitadas = {linha["n_aih"]: linha for linha in adapters["RJ"].ler(caminhos["RJ"])}
        motivos = {(linha["n_aih"], linha["codigo_erro"]): linha for linha in adapters["ER"].ler(caminhos["ER"])}

        for modelo in (SihHospitalMonth, SihHospitalProcedureMonth, SihApprovedAih, SihRejection, SihRejectionReason):
            db.execute(delete(modelo).where(modelo.uf == uf, modelo.competencia == competencia))

        por_procedimento: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(lambda: {
            "aih": 0, "valor": 0.0, "diarias": 0, "diarias_uti": 0, "permanencia_dias": 0,
        })
        for linha in aprovadas.values():
            p = por_procedimento[(linha["cnes"], linha["proc_realizado"] or "", linha.get("complexidade") or "")]
            p["aih"] += 1
            p["valor"] += linha["valor"]
            p["diarias"] += linha["diarias"]
            p["diarias_uti"] += linha["diarias_uti"]
            p["permanencia_dias"] += linha["permanencia"]

        por_hospital: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "aih_aprovadas": 0, "valor_aprovado": 0.0, "aih_rejeitadas": 0, "valor_rejeitado": 0.0,
            "diarias": 0, "diarias_uti": 0, "permanencia_dias": 0,
        })
        for linha in aprovadas.values():
            h = por_hospital[linha["cnes"]]
            h["aih_aprovadas"] += 1
            h["valor_aprovado"] += linha["valor"]
            h["diarias"] += linha["diarias"]
            h["diarias_uti"] += linha["diarias_uti"]
            h["permanencia_dias"] += linha["permanencia"]
        for linha in rejeitadas.values():
            h = por_hospital[linha["cnes"]]
            h["aih_rejeitadas"] += 1
            h["valor_rejeitado"] += linha["valor"]

        base = {"uf": uf, "competencia": competencia}
        _inserir(db, SihHospitalMonth, [
            {**base, "cnes": cnes, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in h.items()}}
            for cnes, h in por_hospital.items()
        ])
        _inserir(db, SihHospitalProcedureMonth, [
            {**base, "cnes": cnes, "proc_realizado": proc, "complexidade": complexidade,
             **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items()}}
            for (cnes, proc, complexidade), p in por_procedimento.items()
        ])
        _inserir(db, SihApprovedAih, [{**base, "cnes": l["cnes"], "n_aih": l["n_aih"]} for l in aprovadas.values()])
        _inserir(db, SihRejection, [
            {**base, "cnes": l["cnes"], "n_aih": l["n_aih"], "competencia_aih": l["competencia_aih"],
             "proc_realizado": l["proc_realizado"], "valor": l["valor"], "dt_internacao": l["dt_internacao"],
             "dt_saida": l["dt_saida"], "marca_uti": l["marca_uti"]}
            for l in rejeitadas.values()
        ])
        _inserir(db, SihRejectionReason, [
            {**base, "cnes": l["cnes"], "n_aih": l["n_aih"], "codigo_erro": l["codigo_erro"]} for l in motivos.values()
        ])

        for tipo, quantidade in (("RD", len(aprovadas)), ("RJ", len(rejeitadas)), ("ER", len(motivos))):
            cargas[tipo].linhas = quantidade
            cargas[tipo].status = "OK"
            cargas[tipo].concluido_em = _agora()
        db.commit()
        logger.info("SIH %s %s: %d aprovadas, %d rejeitadas, %d motivos, %d hospitais",
                    uf, competencia, len(aprovadas), len(rejeitadas), len(motivos), len(por_hospital))
        return ResultadoCarga(uf, competencia, len(aprovadas), len(rejeitadas), len(motivos), len(por_hospital))
    except Exception as exc:
        db.rollback()
        for carga in cargas.values():
            carga.status = "FAILED"
            carga.erro = f"{exc.__class__.__name__}: {exc}"[:2000]
            carga.concluido_em = _agora()
        db.commit()
        raise
    finally:
        for arquivo in baixados:
            arquivo.unlink(missing_ok=True)


def competencias_para_carga(adapters: dict[str, DataSourceAdapter], uf: str, quantidade: int) -> list[str]:
    """As mais recentes que têm RD, RJ e ER publicados, da mais antiga para a mais nova."""
    comuns = set.intersection(*(set(adapters[t].competencias_disponiveis(uf)) for t in TIPOS))
    return sorted(comuns)[-quantidade:] if quantidade > 0 else []


def carregar_codigos_de_erro(db: Session, *, pasta_local: Path | None = None, pasta_trabalho: Path | None = None) -> int:
    carga = DataLoad(fonte="SIH_ERROS", origem=(OrigemArquivo.UPLOAD if pasta_local else OrigemArquivo.DOWNLOAD).value)
    db.add(carga)
    db.commit()
    with tempfile.TemporaryDirectory() as temporaria:
        destino = Path(pasta_trabalho or temporaria)
        try:
            caminho = obter_tab_sih(destino, pasta_local=pasta_local)
            codigos = ler_codigos_de_erro(caminho)
            for codigo, descricao in codigos.items():
                db.merge(SihErrorCode(codigo=codigo, descricao=descricao[:255]))
            carga.arquivo, carga.checksum = caminho.name, datasus.sha256(caminho)
            carga.linhas, carga.status, carga.concluido_em = len(codigos), "OK", _agora()
            db.commit()
            if not pasta_local:
                caminho.unlink(missing_ok=True)
            return len(codigos)
        except Exception as exc:
            db.rollback()
            carga.status, carga.erro, carga.concluido_em = "FAILED", str(exc)[:2000], _agora()
            db.commit()
            raise


def atualizar_estabelecimentos(db: Session, cnes: list[str], cliente: CnesDadosAbertos, *,
                               todos: bool = False) -> int:
    """
    Nome e cadastro dos CNES pela API de dados abertos.

    Só os que ainda não estão na base, salvo `todos`. Falha num CNES não para
    os outros: o nome ausente aparece como o número, e a carga fica registrada.
    """
    pedidos = sorted({str(c).zfill(7) for c in cnes})
    if not todos:
        existentes = set(db.execute(select(Establishment.cnes).where(Establishment.cnes.in_(pedidos))).scalars())
        pedidos = [c for c in pedidos if c not in existentes]
    carga = DataLoad(fonte="CNES_API", origem=OrigemArquivo.API.value)
    db.add(carga)
    db.commit()

    gravados, falhas = 0, []
    for numero in pedidos:
        try:
            dados = cliente.estabelecimento(numero)
        except Exception as exc:  # noqa: BLE001 — um CNES com problema não para a carga
            falhas.append(f"{numero}: {exc.__class__.__name__}")
            continue
        if dados:
            db.merge(Establishment(**dados))
            gravados += 1
            if gravados % 50 == 0:
                db.commit()
    carga.linhas = gravados
    carga.status = "OK" if not falhas else "FAILED" if not gravados else "OK"
    carga.erro = f"{len(falhas)} CNES sem resposta: {', '.join(falhas[:20])}" if falhas else None
    carga.concluido_em = _agora()
    db.commit()
    return gravados


def carregar_uf(db: Session, uf: str, *, competencias: list[str] | None = None, quantidade: int = 3,
                adapters: dict[str, DataSourceAdapter] | None = None, pasta_local: Path | None = None,
                cnes_api: CnesDadosAbertos | None = None) -> list[ResultadoCarga]:
    uf = validar_uf(uf)
    adapters = adapters or adapters_padrao(pasta_local)
    lista = sorted(set(competencias)) if competencias else competencias_para_carga(adapters, uf, quantidade)
    if not lista:
        raise datasus.ArquivoIndisponivel(f"Nenhuma competência do SIH com RD, RJ e ER publicada para {uf}")

    with tempfile.TemporaryDirectory() as pasta_trabalho:
        resultados = [carregar_competencia(db, uf, c, adapters, Path(pasta_trabalho)) for c in lista]

    if cnes_api is not None:
        cnes = db.execute(select(SihHospitalMonth.cnes).where(SihHospitalMonth.uf == uf).distinct()).scalars().all()
        atualizar_estabelecimentos(db, list(cnes), cnes_api)
    return resultados


def _codigos_sem_parar(db: Session, pasta_local: Path | None = None) -> int | None:
    """
    Descrição dos motivos, sem derrubar a carga do SIH se falhar.

    Sem descrição o motivo aparece pelo código; sem a carga, não aparece nada.
    A falha fica registrada em data_loads.
    """
    try:
        return carregar_codigos_de_erro(db, pasta_local=pasta_local)
    except Exception:  # noqa: BLE001
        logger.exception("Descrição dos motivos de rejeição não carregada; a carga do SIH segue")
        return None


def job_carregar_uf(uf: str, competencias: list[str] | None = None, quantidade: int = 3) -> list[dict[str, Any]]:
    """
    Entrada da fila (RQ): SIH do FTP, nomes pela API do CNES, leitos e
    habilitações do CNES e o scan recalculado para a UF.
    """
    from sqlalchemy.orm import sessionmaker

    from app.db import engine
    from app.jobs.carga_cnes import carregar_cnes_uf
    from app.jobs.recalcular import recalcular

    Sessao = sessionmaker(bind=engine(), expire_on_commit=False)
    with Sessao() as db, CnesDadosAbertos() as cnes_api:
        if db.execute(select(SihErrorCode.codigo).limit(1)).first() is None:
            _codigos_sem_parar(db)
        resultados = carregar_uf(db, uf, competencias=competencias, quantidade=quantidade, cnes_api=cnes_api)
        try:
            carregar_cnes_uf(db, uf)
        except Exception:  # noqa: BLE001 — sem leitos o scan sai sem porte, mas sai
            logger.exception("Leitos e habilitações do CNES de %s não carregados", uf)
        recalcular(db, ufs=[validar_uf(uf)])
    return [r.__dict__ for r in resultados]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga do SIH/SUS por UF.")
    parser.add_argument("--uf", required=True,
                        help="UFs separadas por vírgula (ex.: CE,PE) ou TODAS para as 27")
    parser.add_argument("--competencias", help="AAAAMM separadas por vírgula; sem isto, as mais recentes publicadas")
    parser.add_argument("--quantidade", type=int, default=3, help="Quantas competências recentes (padrão 3)")
    parser.add_argument("--pasta", type=Path, help="Pasta com os .dbc e o TAB_SIH.zip já baixados, em vez do FTP")
    parser.add_argument("--sem-cnes", action="store_true", help="Não consulta nomes na API de dados abertos do CNES")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    competencias = [c.strip() for c in args.competencias.split(",")] if args.competencias else None
    Sessao = sessionmaker(bind=engine(), expire_on_commit=False)
    cnes_api = None if args.sem_cnes else CnesDadosAbertos()
    try:
        with Sessao() as db:
            codigos = _codigos_sem_parar(db, pasta_local=args.pasta)
            print(f"Motivos de rejeição: {codigos if codigos is not None else 'falhou (ver data_loads)'}")
            falhas = []
            for uf in ufs_do_argumento(args.uf):
                # Uma UF que falha (arquivo ainda não publicado, FTP instável)
                # não impede as outras; a falha fica em data_loads.
                try:
                    for r in carregar_uf(db, uf, competencias=competencias, quantidade=args.quantidade,
                                         pasta_local=args.pasta, cnes_api=cnes_api):
                        print(f"{r.uf} {r.competencia}: {r.aprovadas} aprovadas, {r.rejeitadas} rejeitadas, "
                              f"{r.motivos} motivos, {r.hospitais} hospitais")
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Carga de %s falhou", uf)
                    falhas.append(f"{uf}: {exc}")
    finally:
        if cnes_api:
            cnes_api.close()
    if falhas:
        print("UFs com falha:\n  " + "\n  ".join(falhas))
        return 1
    return 0


def ufs_do_argumento(texto: str) -> list[str]:
    if texto.strip().upper() in {"TODAS", "BR", "BRASIL"}:
        return list(CODIGO_UF)
    return [validar_uf(uf) for uf in texto.split(",") if uf.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
