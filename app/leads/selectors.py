"""Consultas de leads reutilizadas pelas telas do painel e pela API (somente leitura)."""

import datetime

from django.db.models import Q
from django.utils import timezone

from .models import ClassificacaoLead, Lead, LeadStatus

SEM_CLASSIFICACAO = "sem"
# Colunas da listagem: lê só a tabela lead (snapshot), sem JOIN (Entregável 7).
CAMPOS_LISTAGEM = [
    "id", "nome", "email", "telefone", "empresa", "cargo", "created_at", "classificacao_atual", "status_atual",
]
# Detalhe: no máximo os N eventos mais recentes de cada histórico.
LIMITE_HISTORICO = 100


def _inicio_do_dia(data):
    return timezone.make_aware(datetime.datetime.combine(data, datetime.time.min))


def filtrar_leads(*, status=None, classificacao=None, inicio=None, fim=None, busca=None):
    """Leads ativos com os filtros do painel, do mais recente para o mais antigo.

    Valores fora das choices são ignorados (nunca chegam ao SQL). `inicio`/`fim` são datas
    (fim inclusivo, convertido em limite aberto no fuso do projeto). Usa os índices parciais
    ix_lead_status_periodo, ix_lead_classificacao_periodo e ix_lead_listagem.
    """
    qs = Lead.objects.ativos()
    if status in LeadStatus.values:
        qs = qs.filter(status_atual=status)
    if classificacao == SEM_CLASSIFICACAO:
        qs = qs.filter(classificacao_atual__isnull=True)
    elif classificacao in ClassificacaoLead.values:
        qs = qs.filter(classificacao_atual=classificacao)
    if isinstance(inicio, datetime.date):
        qs = qs.filter(created_at__gte=_inicio_do_dia(inicio))
    if isinstance(fim, datetime.date):
        qs = qs.filter(created_at__lt=_inicio_do_dia(fim + datetime.timedelta(days=1)))
    if busca:
        # icontains escapa % e _ e é parametrizado; o tamanho do termo é limitado no form (P13).
        qs = qs.filter(Q(nome__icontains=busca) | Q(email__icontains=busca) | Q(empresa__icontains=busca))
    return qs.only(*CAMPOS_LISTAGEM).order_by("-created_at", "-id")


def leads_ativos():
    """Base do detalhe: excluídos e anonimizados ficam de fora (404)."""
    return Lead.objects.ativos()


def historico_status(lead, limite=LIMITE_HISTORICO):
    return lead.historico_status.select_related("sistema", "usuario").order_by("-ocorrido_em", "-id")[:limite]


def historico_classificacoes(lead, limite=LIMITE_HISTORICO):
    return lead.classificacoes.select_related("sistema", "usuario").order_by("-classificado_em", "-id")[:limite]
