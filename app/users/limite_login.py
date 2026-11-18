"""Limite de tentativas de login com senha errada (tela, admin e Basic Auth da API).

Duas contagens em janela fixa no cache "default" (Redis em produção), com `add` + `incr`
atômicos (mesmo padrão de app/leads/throttles.py):
- por IP (IPv6 agrupado por /64): LOGIN_MAX_FALHAS_IP falhas por janela, qualquer conta;
- por conta + IP: LOGIN_MAX_FALHAS_CONTA falhas por janela para o mesmo username.
A conta é contada junto com o IP de propósito: um atacante não consegue bloquear a vítima
nos outros aparelhos dela (bloqueio de conta como negação de serviço).

Conta também usernames inexistentes (mesmo custo nos dois casos; não diferencia contas).
O username entra na chave só como hash. Enquanto bloqueado, a tentativa nem verifica a
senha, não grava em registro_acesso (evita inundar a tabela) e só gera o evento
"login_bloqueado" no log. Login bem-sucedido zera a contagem da conta naquele IP.

Falha do cache não impede o login (fail-open, como na captação de leads): o evento
"limite_login_indisponivel" vai para o log.
"""

import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import RedisError

from .models import normalizar_username

logger = logging.getLogger("gohub.auditoria")

ATRIBUTO_BLOQUEIO = "_gohub_login_bloqueado"


def _ip(request):
    from app.leads.throttles import identificador, ip_cliente

    return identificador(ip_cliente(request))


def _chaves(request, username):
    janela = int(time.time() // settings.LOGIN_JANELA_SEGUNDOS)
    ip = _ip(request)
    conta = hashlib.sha256(normalizar_username(username).encode()).hexdigest()[:32]
    return (
        f"login_falhas:{janela}:ip:{ip}",
        f"login_falhas:{janela}:conta:{conta}:{ip}",
    )


def _limites():
    return settings.LOGIN_MAX_FALHAS_IP, settings.LOGIN_MAX_FALHAS_CONTA


def bloqueado(request, username):
    """True se o IP ou a conta+IP já atingiu o limite de falhas na janela atual."""
    if request is None:
        return False
    try:
        valores = cache.get_many(_chaves(request, username))
    except (RedisError, OSError):
        logger.warning("evento=limite_login_indisponivel")
        return False
    chave_ip, chave_conta = _chaves(request, username)
    limite_ip, limite_conta = _limites()
    return (valores.get(chave_ip) or 0) >= limite_ip or (valores.get(chave_conta) or 0) >= limite_conta


def _incrementar(chave):
    timeout = settings.LOGIN_JANELA_SEGUNDOS + 1
    cache.add(chave, 0, timeout=timeout)
    try:
        cache.incr(chave)
    except ValueError:  # expirou entre o add e o incr
        cache.set(chave, 1, timeout=timeout)


def registrar_falha(request, username):
    if request is None:
        return
    try:
        for chave in _chaves(request, username):
            _incrementar(chave)
    except (RedisError, OSError):
        logger.warning("evento=limite_login_indisponivel")


def limpar_conta(request, username):
    """Após login bem-sucedido: zera só a contagem da conta neste IP (a do IP continua)."""
    if request is None:
        return
    try:
        cache.delete(_chaves(request, username)[1])
    except (RedisError, OSError):
        logger.warning("evento=limite_login_indisponivel")


def marcar_bloqueio(request):
    if request is not None:
        setattr(request, ATRIBUTO_BLOQUEIO, True)


def foi_bloqueado(request):
    return bool(getattr(request, ATRIBUTO_BLOQUEIO, False))
