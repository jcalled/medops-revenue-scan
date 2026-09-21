"""
Área do contrato: os arquivos que o hospital ou a OSS entregou, do tenant.

Fica à parte do dado público do DATASUS. A administração da plataforma não entra
aqui — como no núcleo, que recusa dado de hospital a quem não é do tenant: quem
sobe e vê os arquivos é o usuário do cliente (ou o tenant de operação da MedOps).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.organizacoes import organizacao_no_escopo
from app.db import get_db
from app.domain.contrato import LEITURAS, TAMANHO_MAXIMO, TIPOS
from app.models import ContractFile

router = APIRouter(prefix="/api/revenue-scan/contract", tags=["contrato"])


def require_tenant(acesso: Acesso = Depends(require_revenue_scan)) -> Acesso:
    if acesso.principal.tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="A Área do contrato guarda dado de hospital e é do cliente: entre com um usuário do "
                                   "tenant. A administração da plataforma não acessa dado de tenant.")
    return acesso


def _json(a: ContractFile) -> dict[str, Any]:
    return {
        "id": a.id, "organization_id": a.organization_id, "cnes": a.cnes, "tipo": a.tipo,
        "tipo_nome": TIPOS.get(a.tipo, {}).get("titulo", a.tipo), "competencia": a.competencia, "nome": a.nome,
        "tamanho": a.tamanho, "sha256": a.sha256, "analysis_id": a.analysis_id, "resumo": a.resumo,
        "enviado_em": a.enviado_em.isoformat() if a.enviado_em else None,
    }


def _arquivos(db: Session, acesso: Acesso, organizacao: int | None) -> list[ContractFile]:
    consulta = select(ContractFile).where(ContractFile.tenant_id == acesso.principal.tenant_id)
    if organizacao is not None:
        consulta = consulta.where(ContractFile.organization_id == organizacao)
    return list(db.execute(consulta.order_by(ContractFile.enviado_em.desc(), ContractFile.id.desc())).scalars())


@router.get("/checklist")
def lista_do_que_pedir(organizacao: int | None = Query(default=None), acesso: Acesso = Depends(require_tenant),
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    if organizacao is not None:
        organizacao_no_escopo(db, acesso, organizacao)
    arquivos = _arquivos(db, acesso, organizacao)
    return {"itens": [{
        "tipo": tipo, **{k: v for k, v in dados.items()}, "leitura_nome": LEITURAS[str(dados["leitura"])],
        "enviados": sum(1 for a in arquivos if a.tipo == tipo),
        "ultimo_envio": next((a.enviado_em.isoformat() for a in arquivos if a.tipo == tipo and a.enviado_em), None),
    } for tipo, dados in TIPOS.items()], "tamanho_maximo": TAMANHO_MAXIMO}


@router.get("/files")
def listar(organizacao: int | None = Query(default=None), acesso: Acesso = Depends(require_tenant),
           db: Session = Depends(get_db)) -> dict[str, Any]:
    if organizacao is not None:
        organizacao_no_escopo(db, acesso, organizacao)
    return {"arquivos": [_json(a) for a in _arquivos(db, acesso, organizacao)]}


@router.post("/files", status_code=status.HTTP_201_CREATED)
async def enviar(
    tipo: str = Form(...),
    arquivo: UploadFile = File(...),
    organizacao: int | None = Form(default=None),
    cnes: str | None = Form(default=None),
    competencia: str | None = Form(default=None),
    analysis_id: int | None = Form(default=None),
    resumo: str | None = Form(default=None),
    acesso: Acesso = Depends(require_tenant),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Guarda o arquivo do contrato. O SISAIH01 chega depois de conferido no
    FaturaSUS do núcleo (pela própria tela, com o login do cliente): vem com o
    número da análise e o resumo, e o conteúdo fica lá, não aqui.
    """
    if tipo not in TIPOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Tipo desconhecido. Use {', '.join(TIPOS)}.")
    if organizacao is not None:
        organizacao_no_escopo(db, acesso, organizacao)
    if competencia and not (len(competencia) == 6 and competencia.isdigit()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Competência deve ser AAAAMM.")
    nome = Path(arquivo.filename or "arquivo").name[:255]
    if Path(nome).suffix.lower() not in TIPOS[tipo]["formatos"]:  # type: ignore[operator]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{TIPOS[tipo]['titulo']}: envie {', '.join(TIPOS[tipo]['formatos'])}.")  # type: ignore[arg-type]
    conteudo = await arquivo.read(TAMANHO_MAXIMO + 1)
    if not conteudo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Arquivo vazio.")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Arquivo maior que 20 MB: divida por competência.")
    if tipo == "SISAIH01" and analysis_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="O SISAIH01 passa primeiro pelo FaturaSUS; envie pela Área do contrato.")
    try:
        dados_resumo = json.loads(resumo) if resumo else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Resumo inválido.") from exc

    registro = ContractFile(
        tenant_id=acesso.principal.tenant_id, organization_id=organizacao,
        cnes=cnes.strip().zfill(7) if cnes and cnes.strip() else None, tipo=tipo, competencia=competencia or None,
        nome=nome, tamanho=len(conteudo), sha256=hashlib.sha256(conteudo).hexdigest(),
        conteudo=None if tipo == "SISAIH01" else conteudo, analysis_id=analysis_id, resumo=dados_resumo,
        enviado_por=acesso.principal.user_id,
    )
    db.add(registro)
    db.commit()
    return _json(registro)


def _do_tenant(db: Session, acesso: Acesso, arquivo_id: int) -> ContractFile:
    registro = db.get(ContractFile, arquivo_id)
    # Arquivo de outro tenant responde igual a inexistente.
    if registro is None or registro.tenant_id != acesso.principal.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado.")
    return registro


@router.get("/files/{arquivo_id}/download")
def baixar(arquivo_id: int, acesso: Acesso = Depends(require_tenant), db: Session = Depends(get_db)) -> Response:
    registro = _do_tenant(db, acesso, arquivo_id)
    if registro.conteudo is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="O SISAIH01 fica no FaturaSUS: baixe o original ou a versão corrigida pela correção.")
    return Response(registro.conteudo, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{registro.nome}"'})


@router.delete("/files/{arquivo_id}", status_code=status.HTTP_204_NO_CONTENT)
def apagar(arquivo_id: int, acesso: Acesso = Depends(require_tenant), db: Session = Depends(get_db)) -> Response:
    db.delete(_do_tenant(db, acesso, arquivo_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
