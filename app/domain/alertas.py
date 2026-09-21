"""
Alerta mensal por organização: quando um mês novo do DATASUS entra, o que a OSS
precisa saber — o que vence, quanto foi rejeitado no mês, quanto o FaturaSUS
teria pegado antes do envio, a capacidade de cada hospital e o que ainda volta.

Um alerta por organização e mês novo: carregar de novo não repete. Sem e-mail
configurado, o alerta fica na tela de Alertas e diz isso.
"""
from __future__ import annotations

import html as html_mod
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import email as email_adapter
from app.config import Settings, get_settings
from app.domain.kit import referencia_padrao
from app.domain.recuperacao import meses_carregados
from app.domain.relatorio_recuperacao import montar_relatorio
from app.models import AlertIssue, AlertSubscription, Establishment, ManagementOrganization, SihHospitalMonth

# Uso da capacidade a partir do qual o alerta chama atenção: perto do limite, o SIH começa a cancelar.
USO_DE_ATENCAO = 0.9
HOSPITAIS_NO_ALERTA = 10


def _brl(valor: float) -> str:
    return "R$ " + f"{valor:,.0f}".replace(",", ".")


def _mes(aaaamm: str) -> str:
    nomes = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
    return f"{nomes[int(aaaamm[4:]) - 1]}/{aaaamm[:4]}"


def _pegou(grupos: list[dict[str, Any]] | None) -> dict[str, float]:
    achado = next((g for g in grupos or [] if g["grupo"] == "PEGARIA"), None)
    return {"aih": achado["aih"], "valor": achado["valor"]} if achado else {"aih": 0, "valor": 0.0}


def montar_alerta(db: Session, org: ManagementOrganization, referencia: str | None = None,
                  assinatura: AlertSubscription | None = None) -> dict[str, Any] | None:
    cnes = [u.cnes for u in org.unidades]
    if not cnes:
        return None
    meses = meses_carregados(db, cnes)
    if not meses:
        return None
    ref = referencia or referencia_padrao()
    ultimo = meses[-1]
    percentual = float(assinatura.percentual) if assinatura else 15.0
    periodo = montar_relatorio(db, cnes, meses, ref, percentual=percentual)
    mes = montar_relatorio(db, cnes, [ultimo], ref, percentual=percentual)
    t, tm = periodo["total"], mes["total"]
    recuperavel = t["A_RECUPERAR"]["valor"] + t["DEPENDE_GESTOR"]["valor"]
    do_mes = {h["cnes"]: h for h in mes["hospitais"]}
    com_capacidade = {h["cnes"] for h in periodo["hospitais"]
                      if any(p["categoria"] == "CAPACIDADE" and p["rejeitado"]["valor"] > 0 for p in h.get("prevenir") or [])}

    hospitais = []
    for h in periodo["hospitais"]:
        m = do_mes.get(h["cnes"])
        cap = (m or {}).get("capacidade") or {}
        hospitais.append({
            "cnes": h["cnes"], "nome": h["nome"],
            "vence_neste_mes": h["total"]["vence_neste_mes"],
            "rejeitado_mes": m["total"]["rejeitadas"] if m else {"aih": 0, "valor": 0.0},
            "teria_pegado_mes": _pegou(m.get("antes_do_envio")) if m else {"aih": 0, "valor": 0.0},
            "uso_capacidade": cap.get("ocupacao"), "leitos_sus": cap.get("leitos_sus"),
            "recuperavel": round(h["total"]["A_RECUPERAR"]["valor"] + h["total"]["DEPENDE_GESTOR"]["valor"], 2),
            "principal_acao": ({"nome": h["prevenir"][0]["nome"], "por_mes": h["prevenir"][0]["media_mensal"],
                                "como": h["prevenir"][0]["como_evitar"]} if h.get("prevenir") else None),
        })
    hospitais.sort(key=lambda h: -(h["vence_neste_mes"]["valor"] + h["rejeitado_mes"]["valor"]))
    return {
        "organizacao": {"id": org.id, "sigla": org.sigla, "nome": org.nome},
        "competencia": ultimo, "referencia": ref, "gerado_em": date.today().isoformat(),
        "rejeitado_mes": tm["rejeitadas"],
        "teria_pegado_mes": _pegou(mes.get("antes_do_envio")),
        "vence_neste_mes": t["vence_neste_mes"],
        "recuperavel": {"aih": t["A_RECUPERAR"]["aih"] + t["DEPENDE_GESTOR"]["aih"], "valor": round(recuperavel, 2)},
        "recuperado": t["RECUPERADA"],
        "honorarios": ({"percentual": percentual, "sobre_recuperavel": round(recuperavel * percentual / 100, 2)}
                       if assinatura and assinatura.incluir_honorarios else None),
        # Só quem teve rejeição por capacidade no período: a conta leitos × dias é estimativa, não a do SIH.
        "capacidade_atencao": [h for h in hospitais if (h["uso_capacidade"] or 0) >= USO_DE_ATENCAO and h["cnes"] in com_capacidade],
        "hospitais": hospitais[:HOSPITAIS_NO_ALERTA],
    }


def _linha_hospital(h: dict[str, Any]) -> str:
    celula = "padding:6px;border-bottom:1px solid #e3e6eb"
    uso = "" if h["uso_capacidade"] is None else f"{h['uso_capacidade'] * 100:.0f}%"
    valores = (h["vence_neste_mes"]["valor"], h["rejeitado_mes"]["valor"], h["teria_pegado_mes"]["valor"])
    numeros = "".join(f"<td style='{celula};text-align:right'>{_brl(v)}</td>" for v in valores)
    return (f"<tr><td style='{celula}'>{html_mod.escape(h['nome'] or h['cnes'])}</td>{numeros}"
            f"<td style='{celula};text-align:right'>{uso}</td></tr>")


def renderizar(conteudo: dict[str, Any], app_url: str) -> tuple[str, str, str]:
    """Assunto, HTML e texto do e-mail. HTML simples, com estilo inline: é o que os clientes de e-mail aceitam."""
    e = html_mod.escape
    org = conteudo["organizacao"]
    mes = _mes(conteudo["competencia"])
    pegou, vence, rej = conteudo["teria_pegado_mes"], conteudo["vence_neste_mes"], conteudo["rejeitado_mes"]
    assunto = f"{org['sigla']}: {_brl(vence['valor'])} vencem neste mês · SUS {mes}"
    link = f"{app_url}/revenue-scan/prevencao"

    linhas_hosp = "".join(_linha_hospital(h) for h in conteudo["hospitais"])
    capacidade = "".join(
        f"<li>{e(h['nome'] or h['cnes'])}: cerca de {h['uso_capacidade'] * 100:.0f}% do limite estimado (leitos SUS do CNES × dias), com rejeição por capacidade no período</li>"
        for h in conteudo["capacidade_atencao"])
    honorarios = (f"<p style='margin:4px 0'>Honorários MedOps ({conteudo['honorarios']['percentual']:.0f}%) sobre o recuperável: "
                  f"<b>{_brl(conteudo['honorarios']['sobre_recuperavel'])}</b> (estimativa)</p>") if conteudo.get("honorarios") else ""
    corpo = f"""
<div style="font-family:Arial,Helvetica,sans-serif;color:#101c2c;max-width:680px">
  <p style="font-size:12px;color:#5a6576;margin:0">Alerta mensal · SUS · processamento de {mes}</p>
  <h1 style="font-size:22px;margin:4px 0 12px">{e(org['nome'])}</h1>
  <div style="border:2px solid #3C50E0;border-radius:8px;padding:12px 14px;margin-bottom:12px">
    <p style="margin:0;color:#5a6576;font-size:13px">No mês, o SUS rejeitou {_brl(rej['valor'])} em {rej['aih']} AIH. O FaturaSUS teria pegado antes do envio:</p>
    <p style="margin:4px 0 0;font-size:24px;font-weight:bold">{_brl(pegou['valor'])} <span style="font-size:13px;font-weight:normal">em {pegou['aih']} AIH</span></p>
  </div>
  <p style="margin:4px 0"><b>Vence neste mês:</b> {_brl(vence['valor'])} em {vence['aih']} AIH ainda recuperáveis — reapresentar agora.</p>
  <p style="margin:4px 0"><b>Ainda dá para recuperar:</b> {_brl(conteudo['recuperavel']['valor'])} ({conteudo['recuperavel']['aih']} AIH). Já voltou aprovado: {_brl(conteudo['recuperado']['valor'])}.</p>
  {honorarios}
  {f"<p style='margin:10px 0 4px'><b>Capacidade perto do limite</b></p><ul style='margin:0;padding-left:18px'>{capacidade}</ul>" if capacidade else ""}
  <table style="border-collapse:collapse;width:100%;font-size:12px;margin-top:12px">
    <tr style="color:#5a6576;text-align:left"><th style="padding:6px">Hospital</th><th style="padding:6px;text-align:right">Vence no mês</th>
      <th style="padding:6px;text-align:right">Rejeitado no mês</th><th style="padding:6px;text-align:right">FaturaSUS pegaria</th><th style="padding:6px;text-align:right">Uso estimado dos leitos</th></tr>
    {linhas_hosp}
  </table>
  <p style="margin:14px 0"><a href="{e(link)}" style="background:#3C50E0;color:#fff;padding:9px 14px;border-radius:6px;text-decoration:none;font-weight:bold">Ver a prevenção e o pacote de correção</a></p>
  <p style="font-size:11px;color:#5a6576">Dados públicos do DATASUS (SIH/SUS) e avaliação do FaturaSUS. Valores são oportunidade financeira estimada;
  só contam como recuperados quando voltam aprovados. MedOps · GlosaAI.</p>
</div>"""
    texto = "\n".join([
        f"Alerta mensal · SUS · {mes} · {org['nome']}",
        f"Rejeitado no mês: {_brl(rej['valor'])} ({rej['aih']} AIH). O FaturaSUS teria pegado antes do envio: {_brl(pegou['valor'])} ({pegou['aih']} AIH).",
        f"Vence neste mês: {_brl(vence['valor'])} ({vence['aih']} AIH).",
        f"Ainda dá para recuperar: {_brl(conteudo['recuperavel']['valor'])}. Já voltou aprovado: {_brl(conteudo['recuperado']['valor'])}.",
        *(f"Capacidade: {h['nome']} em cerca de {h['uso_capacidade'] * 100:.0f}% do limite estimado, com rejeição por capacidade." for h in conteudo["capacidade_atencao"]),
        f"Detalhes: {link}",
    ])
    return assunto, corpo, texto


def ufs_das_assinaturas(db: Session) -> set[str]:
    """UFs dos hospitais das organizações com alerta ligado: é o que o agendador mantém em dia."""
    ufs: set[str] = set()
    for assinatura, org in db.execute(select(AlertSubscription, ManagementOrganization)
                                      .join(ManagementOrganization, ManagementOrganization.id == AlertSubscription.organization_id)
                                      .where(AlertSubscription.ativo.is_(True))):
        cnes = [u.cnes for u in org.unidades]
        if org.uf:
            ufs.add(org.uf)
        if cnes:
            ufs |= {u for u in db.execute(select(Establishment.uf).where(Establishment.cnes.in_(cnes))).scalars() if u}
            ufs |= set(db.execute(select(SihHospitalMonth.uf).where(SihHospitalMonth.cnes.in_(cnes)).distinct()).scalars())
    return ufs


def gerar_alertas(db: Session, ufs: list[str] | None = None, *, organizacao: int | None = None, forcar: bool = False,
                  settings: Settings | None = None,
                  enviar: Callable[..., None] = email_adapter.enviar) -> list[AlertIssue]:
    """
    Gera o alerta de cada organização com alerta ligado cujo mês mais recente ainda
    não teve alerta; `forcar` refaz e reenvia (o "enviar agora" da tela).
    """
    settings = settings or get_settings()
    consulta = (select(AlertSubscription, ManagementOrganization)
                .join(ManagementOrganization, ManagementOrganization.id == AlertSubscription.organization_id))
    if organizacao is not None:
        consulta = consulta.where(AlertSubscription.organization_id == organizacao)
    else:
        consulta = consulta.where(AlertSubscription.ativo.is_(True))
    gerados = []
    for assinatura, org in db.execute(consulta).all():
        if ufs:
            cnes = [u.cnes for u in org.unidades]
            ufs_org = set(db.execute(select(SihHospitalMonth.uf).where(SihHospitalMonth.cnes.in_(cnes)).distinct()).scalars()) if cnes else set()
            if not ufs_org & set(ufs):
                continue
        conteudo = montar_alerta(db, org, assinatura=assinatura)
        if conteudo is None:
            continue
        existente = db.execute(select(AlertIssue).where(AlertIssue.organization_id == org.id,
                                                        AlertIssue.competencia == conteudo["competencia"])).scalar_one_or_none()
        if existente and not forcar:
            continue
        emissao = existente or AlertIssue(organization_id=org.id, competencia=conteudo["competencia"])
        emissao.referencia, emissao.conteudo = conteudo["referencia"], conteudo
        emissao.destinatarios, emissao.erro = list(assinatura.emails or []), None
        if not emissao.destinatarios:
            emissao.status = "SEM_DESTINATARIO"
        elif not settings.email_configurado:
            emissao.status = "SO_NA_TELA"
        else:
            assunto, corpo, texto = renderizar(conteudo, settings.app_url)
            try:
                enviar(settings, emissao.destinatarios, assunto, corpo, texto)
                emissao.status = "ENVIADO"
            except Exception as exc:  # noqa: BLE001 — registra e segue para a próxima organização
                emissao.status, emissao.erro = "FALHOU", f"{type(exc).__name__}: {exc}"[:1000]
        if existente is None:
            db.add(emissao)
        db.commit()
        gerados.append(emissao)
    return gerados
