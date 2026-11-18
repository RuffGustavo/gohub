"""Autenticação dos sistemas externos na API interna (/api/interno/), decisões P2 e P3.

Usada SOMENTE nas views internas (authentication_classes = [SistemaTokenAuthentication]);
REST_FRAMEWORK global intocado. Sessão e Basic não valem nessas views.

Cabeçalho: ``Authorization: Bearer <token>``. O token de cada sistema vem de settings.INTEGRACOES
(variáveis de ambiente, nunca do banco). Todas as recusas respondem com a MESMA mensagem
e são auditadas ("integracao_autenticacao_falhou motivo=...") sem o token.

Rotação de credenciais (token e segredo HMAC), sem indisponibilidade:
1. Defina INTEGRACOES_N8N_TOKEN_ANTERIOR (e/ou ..._SEGREDO_HMAC_ANTERIOR) com o valor atual e
   INTEGRACOES_N8N_TOKEN (e/ou ..._SEGREDO_HMAC) com o novo.
2. Reinicie web, worker e beat (docker compose restart web worker beat).
3. Atualize a credencial no n8n.
4. Quando o log "evento=integracao_token_anterior" parar de aparecer, remova as variáveis
   ..._ANTERIOR e reinicie de novo.
O segredo anterior só é aceito na ENTRADA; a saída já assina com o novo.
"""

import hmac
import logging

from django.conf import settings
from rest_framework import authentication, exceptions

from app.leads.models import Sistema
from app.users.auditoria import registrar

from .throttles import contar_falha, espera_por_falhas

logger = logging.getLogger("gohub.integracoes")

MENSAGEM_FALHA = "Credenciais inválidas."
TOKEN_MAX = 512


class SistemaAutenticado:
    """"Usuário" da requisição quando quem chama é um sistema: sem nenhuma permissão de model,
    então nunca passa em LeadPermissions/DjangoModelPermissions (API do painel)."""

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    pk = None
    id = None

    def __init__(self, sistema):
        self.sistema = sistema

    def __str__(self):
        return f"sistema:{self.sistema.codigo}"

    def has_perm(self, perm, obj=None):
        return False

    def has_perms(self, perm_list, obj=None):
        return False

    def has_module_perms(self, app_label):
        return False


def recusar(request, motivo, excecao=exceptions.AuthenticationFailed, mensagem=MENSAGEM_FALHA):
    """Conta a falha por IP, audita (só o motivo) e levanta a exceção do DRF."""
    contar_falha(request)
    registrar("integracao_autenticacao_falhou", request, motivo=motivo)
    raise excecao(mensagem)


def verificar_bloqueio(request):
    """IP com falhas de autenticação acima do limite: 429 antes de olhar credenciais."""
    espera = espera_por_falhas(request)
    if espera is not None:
        raise exceptions.Throttled(wait=espera)


def _tokens_configurados():
    """[(código do sistema, token, é_anterior)] de todos os sistemas que autenticam (PR1: só n8n)."""
    n8n = settings.INTEGRACOES["n8n"]
    return [(Sistema.N8N, n8n["token"], False), (Sistema.N8N, n8n["token_anterior"], True)]


def _procurar(token):
    """Compara com TODOS os tokens em tempo constante, sem retorno antecipado."""
    encontrado = None
    recebido = token.encode()
    for codigo, esperado, anterior in _tokens_configurados():
        if esperado and hmac.compare_digest(recebido, esperado.encode()) and encontrado is None:
            encontrado = (codigo, anterior)
    return encontrado


class SistemaTokenAuthentication(authentication.BaseAuthentication):
    palavra = "Bearer"

    def authenticate(self, request):
        verificar_bloqueio(request)
        cabecalho = authentication.get_authorization_header(request)
        if not cabecalho:
            return None  # IsSistemaIntegrado recusa com a mesma mensagem (401)
        partes = cabecalho.split()
        if len(partes) != 2 or partes[0].lower() != b"bearer" or len(partes[1]) > TOKEN_MAX:
            recusar(request, "token")
        try:
            token = partes[1].decode("ascii")
        except UnicodeDecodeError:
            recusar(request, "token")
        encontrado = _procurar(token)
        if encontrado is None:
            recusar(request, "token")
        codigo, anterior = encontrado
        sistema = Sistema.objects.filter(codigo=codigo, ativo=True).first()
        if sistema is None:
            recusar(request, "inativo")
        if anterior:
            logger.info("evento=integracao_token_anterior sistema=%s", codigo)
        return SistemaAutenticado(sistema), sistema

    def authenticate_header(self, request):
        return self.palavra
