"""Registro de eventos de segurança da conta (logger "gohub.auditoria").

Regras: nunca registrar senha, token nem e-mail completo. Contas são identificadas pelo
id (UUID); e-mails, quando necessários, só mascarados (ver mascarar_email). O IP é dado
pessoal: a retenção segue a política dos logs da infraestrutura.
"""

import logging

logger = logging.getLogger("gohub.auditoria")


def mascarar_email(email):
    """"ana.souza@empresa.com" -> "a***@empresa.com"."""
    if not email or "@" not in email:
        return "***"
    nome, dominio = email.rsplit("@", 1)
    return f"{nome[:1]}***@{dominio}"


def ip_do_request(request):
    # REMOTE_ADDR é o IP de quem conectou no Django (atrás do proxy, o do proxy) até o
    # cabeçalho do proxy ser confirmado como confiável (etapa do django-axes).
    return request.META.get("REMOTE_ADDR", "-") if request is not None else "-"


def registrar(evento, request=None, usuario=None, **dados):
    """Grava um evento como "evento chave=valor ...", sem dados sensíveis."""
    partes = [f"evento={evento}", f"ip={ip_do_request(request)}"]
    if usuario is not None:
        partes.append(f"usuario_id={usuario.pk}")
    partes += [f"{chave}={valor}" for chave, valor in dados.items()]
    logger.info(" ".join(partes))


# ---- Receptores dos sinais de autenticação (conectados em UsersConfig.ready) ----
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(user_logged_in, dispatch_uid="gohub_auditoria_login")
def _login(sender, request, user, **kwargs):
    registrar("login", request, user, caminho=request.path if request else "-")


@receiver(user_logged_out, dispatch_uid="gohub_auditoria_logout")
def _logout(sender, request, user, **kwargs):
    if user is not None:
        registrar("logout", request, user)


@receiver(user_login_failed, dispatch_uid="gohub_auditoria_login_falha")
def _login_falha(sender, credentials, request=None, **kwargs):
    # O texto digitado no campo de usuário NÃO é registrado (pode ser uma senha digitada
    # no campo errado); só se ele corresponde a uma conta, pelo id.
    from django.contrib.auth import get_user_model

    from .models import normalizar_username

    Usuario = get_user_model()
    usuario_id = (
        Usuario.objects.filter(username=normalizar_username(credentials.get("username")))
        .values_list("pk", flat=True)
        .first()
    )
    caminho = request.path if request is not None else "-"
    if usuario_id:
        logger.info("evento=login_falha ip=%s usuario_id=%s caminho=%s", ip_do_request(request), usuario_id, caminho)
    else:
        logger.info("evento=login_falha ip=%s conta=inexistente caminho=%s", ip_do_request(request), caminho)
