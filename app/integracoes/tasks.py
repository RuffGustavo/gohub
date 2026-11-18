"""Tasks Celery das integrações. Argumentos: só o id do lead (str), nunca dados pessoais.

notificar_lead_recebido não usa autoretry_for: a retentativa é da fila no banco
(lead_integracao.proxima_tentativa_em + beat), única fonte da verdade. Periódicas em
settings.CELERY_BEAT_SCHEDULE. Após alterar este arquivo: docker compose restart worker beat.
"""

import logging

from celery import shared_task

from . import services

logger = logging.getLogger("gohub.integracoes")

ANONIMIZACAO_MAX_TENTATIVAS = 5


@shared_task(acks_late=True, time_limit=30, ignore_result=True)
def notificar_lead_recebido(lead_id):
    """Envia o webhook lead.recebido (idempotente: sai sem efeito se não houver o que fazer)."""
    services.executar_notificacao(lead_id)


@shared_task(time_limit=120, ignore_result=True)
def reprocessar_integracoes_pendentes():
    """Beat (60 s): enfileira as notificações vencidas na fila (lote limitado)."""
    services.reprocessar_pendentes()


@shared_task(time_limit=60, ignore_result=True)
def liberar_tentativas_travadas():
    """Beat (5 min): devolve à fila as tentativas em andamento há mais de 10 min."""
    services.liberar_travadas()


@shared_task(bind=True, acks_late=True, time_limit=30, max_retries=ANONIMIZACAO_MAX_TENTATIVAS, ignore_result=True)
def notificar_lead_anonimizado(self, lead_id):
    """Avisa o n8n da anonimização (P13). Sem estado no banco (lead excluído): a retentativa
    de falhas transitórias é do Celery, com o mesmo backoff da fila."""
    resultado = services.executar_aviso_anonimizacao(lead_id)
    if resultado is None or resultado.ok:
        return
    if not resultado.retentavel or self.request.retries >= self.max_retries:
        logger.error(
            "evento=anonimizacao_nao_avisada lead_id=%s sistema=n8n codigo=%s", lead_id, resultado.codigo_erro
        )
        return
    espera = services.calcular_backoff(self.request.retries + 1).total_seconds()
    raise self.retry(countdown=espera)
