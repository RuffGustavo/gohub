"""Helpers e credenciais FICTÍCIAS dos testes de integrações (nada vem do .env).

HTTP de saída sempre simulado (patch de app.integracoes.services.requests.post): nenhum
teste acessa a rede.
"""

from unittest import mock

from django.utils import timezone

from app.leads.models import Lead, Sistema
from app.leads.tests.base import CONFIG_TESTE as CONFIG_LEADS
from app.leads.tests.base import ComCacheLimpo, conceder, criar_lead, criar_usuario, inserir_lead_sql  # noqa: F401

from ..configuracao import carregar
from ..models import LeadIntegracao

TOKEN = "token-ficticio-de-teste-0123456789abcdefghij"
TOKEN_ANTERIOR = "token-anterior-ficticio-0123456789abcdefghij"
SEGREDO = "segredo-ficticio-de-teste-0123456789abcdefgh"
SEGREDO_ANTERIOR = "segredo-anterior-ficticio-0123456789abcdefgh"
URL = "https://n8n.exemplo.invalid/webhook/gohub-teste"

ENV_TESTE = {
    "INTEGRACOES_N8N_TOKEN": TOKEN,
    "INTEGRACOES_N8N_SEGREDO_HMAC": SEGREDO,
    "INTEGRACOES_N8N_WEBHOOK_URL": URL,
    "INTEGRACOES_MAX_TENTATIVAS": "5",
    "INTEGRACOES_INTERNO_TAXA": "1000/min",
    "INTEGRACOES_AUTH_FALHA_TAXA": "1000/min",
}


def integracoes(**env):
    """settings.INTEGRACOES montado pela mesma função da carga do settings."""
    return carregar({**ENV_TESTE, **env})


INTEGRACOES_TESTE = integracoes()
INTEGRACOES_DESLIGADAS = carregar({})
CONFIG_TESTE = {**CONFIG_LEADS, "INTEGRACOES": INTEGRACOES_TESTE}

# Valores que nunca podem aparecer em logs, respostas ou eventos.
SEGREDOS = (TOKEN, TOKEN_ANTERIOR, SEGREDO, SEGREDO_ANTERIOR, URL, "n8n.exemplo.invalid")


def resposta_http(status=200):
    resposta = mock.Mock(status_code=status)
    resposta.close = mock.Mock()
    return resposta


def patch_post(**kwargs):
    return mock.patch("app.integracoes.services.requests.post", **kwargs)


def sistema(codigo):
    return Sistema.objects.get(codigo=codigo)


def integracao_n8n(lead):
    return LeadIntegracao.objects.get(lead=lead, sistema__codigo=Sistema.N8N)


def novo_lead(email="ana@empresa.com", **dados):
    return criar_lead(email=email, **dados)


def agora_iso():
    return timezone.now().isoformat()


def anonimizar(lead):
    lead = Lead.objects.get(pk=lead.pk)
    lead.anonimizar()
    return lead
