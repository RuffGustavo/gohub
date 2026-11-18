"""Consultas das integrações para telas, API e detalhe do lead (somente leitura).

Sempre só de leads ativos (excluídos/anonimizados não aparecem), com select_related("sistema"),
.only() das colunas exibidas e ordenação estável (desempate por id).
"""

import datetime

from django.db.models import Count, Max
from django.utils import timezone

from app.leads.models import Sistema

from .models import TIPOS_EVENTO, EventoIntegracao, LeadIntegracao, ResultadoEvento, StatusIntegracao

LIMITE_EVENTOS_LEAD = 20
STATUS_FILA_PADRAO = (StatusIntegracao.PENDENTE, StatusIntegracao.ERRO)
CAMPOS_INTEGRACAO = [
    "id", "lead", "sistema", "sistema__codigo", "sistema__nome", "status", "id_externo", "tentativas",
    "ultima_tentativa_em", "proxima_tentativa_em", "sucesso_em", "ultimo_erro", "created_at", "updated_at",
]
CAMPOS_EVENTO_LISTA = [
    "id", "lead", "sistema", "sistema__codigo", "sistema__nome", "tipo_evento", "resultado",
    "chave_idempotencia", "id_externo", "ocorrido_em", "recebido_em",
]


def _inicio_do_dia(data):
    return timezone.make_aware(datetime.datetime.combine(data, datetime.time.min))


def _periodo(qs, campo, inicio, fim):
    if isinstance(inicio, datetime.date):
        qs = qs.filter(**{f"{campo}__gte": _inicio_do_dia(inicio)})
    if isinstance(fim, datetime.date):
        qs = qs.filter(**{f"{campo}__lt": _inicio_do_dia(fim + datetime.timedelta(days=1))})
    return qs


def integracoes_ativas():
    """Base de lead_integracao de leads ativos (detalhe e ações)."""
    return LeadIntegracao.objects.filter(lead__deleted_at__isnull=True).select_related("sistema")


def eventos_ativos():
    return EventoIntegracao.objects.filter(lead__deleted_at__isnull=True).select_related("sistema")


def filtrar_integracoes(*, status=None, sistema=None, lead=None, inicio=None, fim=None):
    """Fila de integrações. `status` é uma lista de valores válidos (vazia = todos)."""
    qs = integracoes_ativas()
    status = [valor for valor in (status or []) if valor in StatusIntegracao.values]
    if status:
        qs = qs.filter(status__in=status)
    if sistema:
        qs = qs.filter(sistema__codigo=sistema)
    if lead:
        qs = qs.filter(lead_id=lead)
    qs = _periodo(qs, "ultima_tentativa_em", inicio, fim)
    return qs.only(*CAMPOS_INTEGRACAO).order_by("-updated_at", "-id")


def filtrar_eventos(*, sistema=None, resultado=None, tipo_evento=None, lead=None, inicio=None, fim=None):
    """Log de eventos, mais recentes primeiro (ix_evento_painel)."""
    qs = eventos_ativos()
    if sistema:
        qs = qs.filter(sistema__codigo=sistema)
    if resultado in ResultadoEvento.values:
        qs = qs.filter(resultado=resultado)
    if tipo_evento in TIPOS_EVENTO:
        qs = qs.filter(tipo_evento=tipo_evento)
    if lead:
        qs = qs.filter(lead_id=lead)
    qs = _periodo(qs, "recebido_em", inicio, fim)
    return qs.only(*CAMPOS_EVENTO_LISTA).order_by("-recebido_em", "-id")


def resumo_por_sistema(agora=None):
    """Cards da visão geral em 3 consultas: sistemas, estado agregado e eventos de 24 h."""
    agora = agora or timezone.now()
    sistemas = list(Sistema.objects.order_by("codigo"))
    resumo = {
        sistema.pk: {
            "sistema": sistema,
            "status": {valor: 0 for valor in StatusIntegracao.values},
            "ultimo_sucesso_em": None,
            "eventos_24h": {ResultadoEvento.SUCESSO: 0, ResultadoEvento.ERRO: 0},
        }
        for sistema in sistemas
    }
    estados = (
        LeadIntegracao.objects.filter(lead__deleted_at__isnull=True)
        .values("sistema_id", "status")
        .annotate(total=Count("id"), ultimo_sucesso=Max("sucesso_em"))
        .order_by()
    )
    for linha in estados:
        item = resumo.get(linha["sistema_id"])
        if item is None:
            continue
        item["status"][linha["status"]] = linha["total"]
        if linha["ultimo_sucesso"] and (
            item["ultimo_sucesso_em"] is None or linha["ultimo_sucesso"] > item["ultimo_sucesso_em"]
        ):
            item["ultimo_sucesso_em"] = linha["ultimo_sucesso"]
    eventos = (
        EventoIntegracao.objects.filter(
            recebido_em__gte=agora - datetime.timedelta(hours=24),
            resultado__in=[ResultadoEvento.SUCESSO, ResultadoEvento.ERRO],
        )
        .values("sistema_id", "resultado")
        .annotate(total=Count("id"))
        .order_by()
    )
    for linha in eventos:
        item = resumo.get(linha["sistema_id"])
        if item is not None:
            item["eventos_24h"][linha["resultado"]] = linha["total"]
    return [resumo[sistema.pk] for sistema in sistemas]


def integracoes_do_lead(lead, limite=LIMITE_EVENTOS_LEAD, com_eventos=True):
    """Seção do detalhe do lead (P18): estado por sistema e, se `com_eventos`, os últimos
    eventos (até 2 consultas)."""
    estados = list(
        LeadIntegracao.objects.filter(lead=lead).select_related("sistema").only(*CAMPOS_INTEGRACAO).order_by(
            "sistema__codigo"
        )
    )
    if not com_eventos:
        return estados, []
    eventos = list(
        EventoIntegracao.objects.filter(lead=lead)
        .select_related("sistema")
        .only(*CAMPOS_EVENTO_LISTA, "mensagem_erro")
        .order_by("-ocorrido_em", "-id")[:limite]
    )
    return estados, eventos
