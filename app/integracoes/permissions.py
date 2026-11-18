"""Permissões da API interna (n8n -> aplicação). A API do painel usa
app.leads.permissions.LeadPermissions (exige view_* também em GET).

Ordem nas views internas: RedePermitida (em initial, antes da autenticação) -> token ->
IsSistemaIntegrado -> AssinaturaHMACValida.
Toda recusa é contada por IP e auditada (authentication.recusar).
"""

import ipaddress

from django.conf import settings
from rest_framework import exceptions
from rest_framework.permissions import BasePermission

from app.leads.models import Sistema
from app.leads.throttles import ip_cliente

from .assinatura import assinatura_valida, mensagem_entrada, timestamp_valido
from .authentication import SistemaAutenticado, recusar


class IsSistemaIntegrado(BasePermission):
    """Só o n8n autenticado por token (PR1). Usuário de sessão nunca chega aqui."""

    def has_permission(self, request, view):
        sistema = request.auth
        if (
            not isinstance(request.user, SistemaAutenticado)
            or not isinstance(sistema, Sistema)
            or sistema.codigo != Sistema.N8N
        ):
            recusar(request, "token")
        return True


class RedePermitida(BasePermission):
    """Decisão P4: IP do chamador dentro de INTEGRACOES_N8N_REDES_PERMITIDAS (vazia = qualquer
    rede, com aviso integracoes.W001 no check). X-Forwarded-For só vale de proxy confiável."""

    def has_permission(self, request, view):
        redes = settings.INTEGRACOES["n8n"]["redes_permitidas"]
        if not redes:
            return True
        try:
            endereco = ipaddress.ip_address(ip_cliente(request))
        except ValueError:
            endereco = None
        if getattr(endereco, "ipv4_mapped", None) is not None:  # ::ffff:a.b.c.d é um IPv4
            endereco = endereco.ipv4_mapped
        if endereco is None or not any(endereco in ipaddress.ip_network(rede) for rede in redes):
            recusar(request, "rede", exceptions.PermissionDenied, "Acesso não permitido.")
        return True


class AssinaturaHMACValida(BasePermission):
    """Decisão P3: X-GoHub-Timestamp (Unix, ±300 s) + X-GoHub-Assinatura (sha256=<hex>) sobre
    "{timestamp}.{MÉTODO}.{caminho com query}.{corpo bruto}", com o segredo atual ou o anterior.
    Vale também para GET (corpo vazio). O caminho é o que o Django recebe (inclui o prefixo de
    SCRIPT_NAME, se houver): o proxy não pode reescrevê-lo."""

    def has_permission(self, request, view):
        n8n = settings.INTEGRACOES["n8n"]
        bruto = request.META.get("HTTP_X_GOHUB_TIMESTAMP")
        timestamp = timestamp_valido(bruto)
        assinatura = request.META.get("HTTP_X_GOHUB_ASSINATURA")
        mensagem = mensagem_entrada(request.method, request.get_full_path(), request.body)
        if timestamp is None or not assinatura_valida(
            mensagem, bruto, assinatura, [n8n["segredo_hmac"], n8n["segredo_hmac_anterior"]]
        ):
            recusar(request, "assinatura")
        return True
