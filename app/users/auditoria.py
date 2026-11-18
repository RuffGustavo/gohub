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
    """IP do cliente para o log: a mesma regra de registro_acesso e do rate limit
    (REMOTE_ADDR; X-Forwarded-For só de proxy confiável, ver LEADS_PROXY_*). "-" se não houver."""
    if request is None:
        return "-"
    return _ip_cliente(request) or "-"


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


def _canal(request):
    from .models import CanalAcesso

    caminho = getattr(request, "path", "") or ""
    if caminho.startswith("/admin/"):
        return CanalAcesso.ADMIN
    if caminho.startswith("/api/"):
        return CanalAcesso.API
    return CanalAcesso.PAINEL


def _ip_cliente(request):
    """IP do cliente com a mesma regra de proxy confiável da captação de leads
    (LEADS_PROXY_SALTOS/LEADS_PROXY_CONFIAVEIS); sem proxy configurado, o REMOTE_ADDR."""
    import ipaddress

    from app.leads.throttles import ip_cliente

    try:
        return str(ipaddress.ip_address(ip_cliente(request)))
    except ValueError:
        return None


def registrar_acesso(request, usuario_id, status):
    """Grava a tentativa em registro_acesso (dashboard). Nunca derruba o login: em erro de
    banco, só registra o evento no log e segue."""
    from django.db import DatabaseError, transaction

    from .agente import resumir_user_agent
    from .models import RegistroAcesso

    navegador, sistema, dispositivo = resumir_user_agent(
        request.META.get("HTTP_USER_AGENT", "") if request is not None else ""
    )
    try:
        with transaction.atomic():
            RegistroAcesso.objects.create(
                usuario_id=usuario_id,
                status=status,
                canal=_canal(request),
                ip=_ip_cliente(request) if request is not None else None,
                navegador=navegador,
                sistema_operacional=sistema,
                dispositivo=dispositivo,
            )
    except DatabaseError:
        logger.warning("evento=registro_acesso_falhou usuario_id=%s status=%s", usuario_id, status)


@receiver(user_logged_in, dispatch_uid="gohub_auditoria_login")
def _login(sender, request, user, **kwargs):
    from .models import StatusAcesso

    from .limite_login import limpar_conta

    registrar("login", request, user, caminho=request.path if request else "-")
    registrar_acesso(request, user.pk, StatusAcesso.SUCESSO)
    limpar_conta(request, user.get_username())


@receiver(user_logged_out, dispatch_uid="gohub_auditoria_logout")
def _logout(sender, request, user, **kwargs):
    if user is not None:
        registrar("logout", request, user)


@receiver(user_login_failed, dispatch_uid="gohub_auditoria_login_falha")
def _login_falha(sender, credentials, request=None, **kwargs):
    # O texto digitado no campo de usuário NÃO é registrado (pode ser uma senha digitada
    # no campo errado); só se ele corresponde a uma conta, pelo id.
    from django.contrib.auth import get_user_model

    from .limite_login import foi_bloqueado, registrar_falha
    from .models import normalizar_username

    caminho = request.path if request is not None else "-"
    if foi_bloqueado(request):
        # Recusada antes de verificar a senha: só o log (não grava em registro_acesso nem conta de novo).
        logger.info("evento=login_bloqueado ip=%s caminho=%s", ip_do_request(request), caminho)
        return
    registrar_falha(request, credentials.get("username"))
    Usuario = get_user_model()
    usuario_id = (
        Usuario.objects.filter(username=normalizar_username(credentials.get("username")))
        .values_list("pk", flat=True)
        .first()
    )
    if usuario_id:
        from .models import StatusAcesso

        logger.info("evento=login_falha ip=%s usuario_id=%s caminho=%s", ip_do_request(request), usuario_id, caminho)
        registrar_acesso(request, usuario_id, StatusAcesso.FALHA)
    else:
        logger.info("evento=login_falha ip=%s conta=inexistente caminho=%s", ip_do_request(request), caminho)
