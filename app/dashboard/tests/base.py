"""Helpers dos testes de dashboard (dados fictícios criados no próprio teste)."""

import datetime

from django.db import connection
from django.test import override_settings
from django.utils import timezone

from app.leads.models import Lead, LeadStatusHistorico, Sistema
from app.leads.tests.base import CONFIG_TESTE, conceder, criar_usuario  # noqa: F401
from app.users.models import RegistroAcesso

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

configuracao = override_settings(**CONFIG_TESTE)


def ha(dias=0, horas=0):
    return timezone.now() - datetime.timedelta(days=dias, hours=horas)


def acesso(usuario, *, quando=None, status="sucesso", ip="203.0.113.10", navegador="Chrome",
           dispositivo="computador", canal="painel"):
    dados = {"usuario": usuario, "status": status, "ip": ip, "navegador": navegador,
             "sistema_operacional": "Windows", "dispositivo": dispositivo, "canal": canal}
    if quando is not None:
        dados["ocorrido_em"] = quando
    return RegistroAcesso.objects.create(**dados)


_SQL_LEAD = (
    "INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao, created_at) "
    "VALUES (%s, %s, '+5511987654321', 'Acme', 'CEO', now(), '1.0', %s) RETURNING id"
)


def lead_em(quando, email):
    """Lead com data de recebimento escolhida (o gatilho grava o status inicial "recebido")."""
    with connection.cursor() as cursor:
        cursor.execute(_SQL_LEAD, ["Lead Teste", email, quando])
        return Lead.objects.get(pk=cursor.fetchone()[0])


def mudar_status(lead, status, codigo_sistema="n8n"):
    LeadStatusHistorico.objects.create(lead=lead, status_novo=status, sistema=Sistema.por_codigo(codigo_sistema))
    lead.refresh_from_db()
    return lead
