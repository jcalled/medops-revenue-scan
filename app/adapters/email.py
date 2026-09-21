"""
Envio de e-mail por SMTP (biblioteca padrão), para os alertas mensais.

Sem SMTP_HOST e SMTP_FROM, não envia: quem chama registra "e-mail não
configurado" e o alerta fica na tela. A senha vem do ambiente e nunca volta na API.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.config import Settings


class EmailNaoConfigurado(Exception):
    pass


def enviar(settings: Settings, destinatarios: list[str], assunto: str, html: str, texto: str) -> None:
    if not settings.email_configurado:
        raise EmailNaoConfigurado("SMTP_HOST e SMTP_FROM não configurados")
    mensagem = EmailMessage()
    mensagem["From"] = settings.smtp_from
    mensagem["To"] = ", ".join(destinatarios)
    mensagem["Subject"] = assunto
    mensagem.set_content(texto)
    mensagem.add_alternative(html, subtype="html")
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(), timeout=30) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(mensagem)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_tls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(mensagem)
