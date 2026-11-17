"""Avisos por e-mail ao titular da conta sobre mudanças de segurança.

Falha no envio não interrompe a operação (só é registrada, sem o endereço completo).
"""

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from .auditoria import logger, mascarar_email, registrar


def _enviar(evento, usuario, destinatario, template, contexto):
    contexto = {"usuario": usuario, "quando": timezone.localtime(timezone.now()), **contexto}
    assunto = "".join(render_to_string(f"users/emails/{template}_assunto.txt", contexto).splitlines())
    corpo = render_to_string(f"users/emails/{template}.txt", contexto)
    try:
        send_mail(assunto, corpo, settings.DEFAULT_FROM_EMAIL, [destinatario])
    except Exception:
        logger.exception("evento=%s_falha_envio usuario_id=%s", evento, usuario.pk)
    else:
        registrar(evento, usuario=usuario, destino=mascarar_email(destinatario))


def avisar_troca_de_email(usuario, email_antigo):
    """Avisa o e-mail ANTIGO: se a troca não foi do titular, ele ainda fica sabendo."""
    _enviar(
        "aviso_email_alterado",
        usuario,
        email_antigo,
        "aviso_email_alterado",
        {"email_novo_mascarado": mascarar_email(usuario.email)},
    )


def avisar_troca_de_senha(usuario):
    _enviar("aviso_senha_alterada", usuario, usuario.email, "aviso_senha_alterada", {})
