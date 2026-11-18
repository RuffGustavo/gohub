"""Performance: plano da consulta da fila sobre volume sintético (ix_lead_integracao_fila).

Informativo: com poucas linhas o planejador pode preferir varredura sequencial. O teste
confirma que o índice parcial atende a consulta (com seqscan desligado) e registra o plano natural.
"""

import logging

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from app.leads.models import Sistema

from ..models import LeadIntegracao, StatusIntegracao
from .base import CONFIG_TESTE

logger = logging.getLogger("gohub.integracoes.testes")

VOLUME = 3000


@override_settings(**CONFIG_TESTE)
class FilaIndiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao) "
                "SELECT 'Lead ' || g, 'lead' || g || '@volume.invalid', '+5511987654321', 'Acme', 'CEO', now(), '1.0' "
                "FROM generate_series(1, %s) AS g",
                [VOLUME],
            )
            # 95% concluídas (fora do índice parcial), 5% pendentes/erro na fila.
            cursor.execute(
                "INSERT INTO lead_integracao (lead_id, sistema_id, status, tentativas, ultima_tentativa_em, "
                "sucesso_em, proxima_tentativa_em) "
                "SELECT l.id, s.id, CASE WHEN row_number() OVER () %% 20 = 0 THEN 'erro' ELSE 'sucesso' END, "
                "1, now(), now(), CASE WHEN row_number() OVER () %% 20 = 0 THEN now() ELSE NULL END "
                "FROM lead l CROSS JOIN sistema s WHERE s.codigo = %s",
                [Sistema.N8N],
            )
            cursor.execute("ANALYZE lead_integracao")

    def plano(self, forcar_indice):
        qs = (
            LeadIntegracao.objects.filter(
                sistema__codigo=Sistema.N8N,
                status__in=[StatusIntegracao.PENDENTE, StatusIntegracao.ERRO],
                proxima_tentativa_em__lte=timezone.now(),
            )
            .order_by("proxima_tentativa_em")
            .values_list("lead_id", flat=True)[:100]
        )
        with connection.cursor() as cursor:
            if forcar_indice:
                cursor.execute("SET LOCAL enable_seqscan = off")
            sql, params = qs.query.sql_with_params()
            cursor.execute("EXPLAIN " + sql, params)
            texto = "\n".join(linha[0] for linha in cursor.fetchall())
            if forcar_indice:
                cursor.execute("SET LOCAL enable_seqscan = on")
        return texto

    def test_indice_parcial_atende_a_fila(self):
        self.assertIn("ix_lead_integracao_fila", self.plano(forcar_indice=True))
        logger.debug("Plano natural da fila:\n%s", self.plano(forcar_indice=False))
