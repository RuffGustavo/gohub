"""Rate limit da captação pública (A4, decisão P6), compartilhado pela tela e pela API.

- Contador em janela fixa no cache "default" (Redis em produção, ver settings.CACHES),
  com `add` + `incr` atômicos: vale entre processos do Gunicorn e não é estourado por
  requisições paralelas (o SimpleRateThrottle do DRF faz ler-modificar-gravar). O valor
  gravado é um inteiro, que o RedisCache do Django armazena sem pickle.
- Cliente identificado pelo IP de quem conectou (REMOTE_ADDR); o X-Forwarded-For só é lido
  com LEADS_PROXY_SALTOS > 0, se a conexão vier de LEADS_PROXY_CONFIAVEIS, e a partir da
  direita (o que fica à esquerda o cliente escreve).
  IPv6 é agrupado por /64 (um usuário costuma controlar o /64 inteiro).
"""

import ipaddress
import logging
import time

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import RedisError
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

logger = logging.getLogger("gohub.leads")

ESCOPO = "leads_publico"


def _de_proxy_confiavel(ip):
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(endereco in ipaddress.ip_network(rede, strict=False) for rede in settings.LEADS_PROXY_CONFIAVEIS)


def ip_cliente(request):
    saltos = settings.LEADS_PROXY_SALTOS
    remoto = request.META.get("REMOTE_ADDR", "")
    if saltos <= 0 or not _de_proxy_confiavel(remoto):
        return remoto
    encaminhados = [ip.strip() for ip in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if ip.strip()]
    if len(encaminhados) < saltos:
        return remoto
    return encaminhados[-saltos]


def identificador(ip):
    """IPv4 inteiro; IPv6 agrupado pela rede /64; valor inválido segue como veio."""
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "-"
    if endereco.version == 6:
        if endereco.ipv4_mapped is not None:  # ::ffff:a.b.c.d é um IPv4
            return str(endereco.ipv4_mapped)
        return str(ipaddress.ip_network(f"{endereco}/64", strict=False))
    return str(endereco)


class LeadPublicoThrottle(BaseThrottle):
    """Até N envios por IP na janela da taxa LEADS_PUBLICO_TAXA (ex.: 10/min)."""

    def __init__(self):
        self.limite, self.duracao = SimpleRateThrottle.parse_rate(None, settings.LEADS_PUBLICO_TAXA)
        self.espera = None

    def get_ident(self, request):
        return identificador(ip_cliente(request))

    def allow_request(self, request, view):
        agora = time.time()
        janela = int(agora // self.duracao)
        chave = f"throttle:{ESCOPO}:{janela}:{self.get_ident(request)}"
        try:
            cache.add(chave, 0, timeout=self.duracao + 1)
            try:
                total = cache.incr(chave)
            except ValueError:  # a chave expirou entre o add e o incr
                cache.set(chave, 1, timeout=self.duracao + 1)
                total = 1
        except (RedisError, OSError):
            # Falha do cache não derruba a captação (fail-open, aprovado); o honeypot continua valendo.
            logger.warning("evento=rate_limit_indisponivel escopo=%s", ESCOPO)
            return True
        if total > self.limite:
            self.espera = self.duracao - (agora % self.duracao)
            return False
        return True

    def wait(self):
        return self.espera


def captacao_permitida(request):
    """Conta uma tentativa de envio (tela ou API) e diz se ainda está dentro do limite."""
    return LeadPublicoThrottle().allow_request(request, None)
