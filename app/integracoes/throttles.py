"""Rate limit da API interna (decisão P16), com a técnica de app/leads/throttles.py.

- Contador em janela fixa no cache "default" com `add` + `incr` atômicos (vale entre processos
  e não é estourado por requisições paralelas). Valor inteiro (sem pickle).
- InternoSistemaThrottle: chamadas por sistema autenticado (INTEGRACOES_INTERNO_TAXA).
- Falhas de autenticação por IP (INTEGRACOES_AUTH_FALHA_TAXA): contadas em cada recusa; acima do
  limite, o IP recebe 429 antes de qualquer verificação de credencial.
- IP pelo mesmo critério da captação (app.leads.throttles.ip_cliente: X-Forwarded-For só de proxy
  confiável), IPv6 agrupado por /64.
- Cache indisponível (decisão P20): os dois contadores deixam passar e registram
  "evento=rate_limit_indisponivel"; token + assinatura HMAC (+ rede) continuam valendo.
"""

import logging
import time

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import RedisError
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

from app.leads.throttles import identificador, ip_cliente

logger = logging.getLogger("gohub.integracoes")

ESCOPO_SISTEMA = "integracoes_interno"
ESCOPO_FALHA = "integracoes_auth_falha"
ESCOPO_TESTE = "integracoes_teste_conexao"
# "Testar conexão" é síncrono (prende um worker até os timeouts): poucos por usuário.
TESTE_CONEXAO_LIMITE = 5
TESTE_CONEXAO_JANELA = 60


def _taxa(nome):
    return SimpleRateThrottle.parse_rate(None, settings.INTEGRACOES[nome])


def _chave(escopo, duracao, ident, agora):
    return f"throttle:{escopo}:{int(agora // duracao)}:{ident}"


def _incrementar(chave, duracao):
    cache.add(chave, 0, timeout=duracao + 1)
    try:
        return cache.incr(chave)
    except ValueError:  # a chave expirou entre o add e o incr
        cache.set(chave, 1, timeout=duracao + 1)
        return 1


def ident_ip(request):
    return identificador(ip_cliente(request))


def espera_por_falhas(request):
    """Segundos até a janela virar, se o IP já excedeu as falhas de autenticação; senão None."""
    limite, duracao = _taxa("auth_falha_taxa")
    agora = time.time()
    try:
        total = cache.get(_chave(ESCOPO_FALHA, duracao, ident_ip(request), agora)) or 0
    except (RedisError, OSError):
        logger.warning("evento=rate_limit_indisponivel escopo=%s", ESCOPO_FALHA)
        return None
    if total >= limite:
        return duracao - (agora % duracao)
    return None


def contar_falha(request):
    _, duracao = _taxa("auth_falha_taxa")
    try:
        _incrementar(_chave(ESCOPO_FALHA, duracao, ident_ip(request), time.time()), duracao)
    except (RedisError, OSError):
        logger.warning("evento=rate_limit_indisponivel escopo=%s", ESCOPO_FALHA)


def teste_conexao_permitido(usuario):
    """Até TESTE_CONEXAO_LIMITE testes por usuário por minuto (cache indisponível: permite)."""
    try:
        total = _incrementar(
            _chave(ESCOPO_TESTE, TESTE_CONEXAO_JANELA, getattr(usuario, "pk", "-"), time.time()), TESTE_CONEXAO_JANELA
        )
    except (RedisError, OSError):
        logger.warning("evento=rate_limit_indisponivel escopo=%s", ESCOPO_TESTE)
        return True
    return total <= TESTE_CONEXAO_LIMITE


class InternoSistemaThrottle(BaseThrottle):
    """Até N chamadas por sistema autenticado na janela de INTEGRACOES_INTERNO_TAXA."""

    def __init__(self):
        self.limite, self.duracao = _taxa("interno_taxa")
        self.espera = None

    def allow_request(self, request, view):
        sistema = getattr(request, "auth", None)
        ident = getattr(sistema, "codigo", None) or ident_ip(request)
        agora = time.time()
        try:
            total = _incrementar(_chave(ESCOPO_SISTEMA, self.duracao, ident, agora), self.duracao)
        except (RedisError, OSError):
            logger.warning("evento=rate_limit_indisponivel escopo=%s", ESCOPO_SISTEMA)
            return True
        if total > self.limite:
            self.espera = self.duracao - (agora % self.duracao)
            return False
        return True

    def wait(self):
        return self.espera
