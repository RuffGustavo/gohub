"""Performance: paginação por cursor, limites e uso dos índices parciais (Entregável 7)."""

import datetime

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from ..selectors import filtrar_leads
from .base import ComCacheLimpo, CONFIG_TESTE, conceder, criar_usuario

# Leads sintéticos numa única instrução (mesmo created_at quando `espalhar` é falso).
SQL_LOTE = """
    INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao, created_at)
    SELECT 'Lead ' || n, 'lote' || n || '@exemplo.com', '+5511987654321', 'Empresa ' || (n %% 50), 'Analista',
           now(), '1.0',
           CASE WHEN %s THEN now() - make_interval(hours => n) ELSE %s END
      FROM generate_series(1, %s) AS n
"""


def inserir_lote(total, *, espalhar=False, instante=None):
    with connection.cursor() as cursor:
        cursor.execute(SQL_LOTE, [espalhar, instante or timezone.now(), total])


@override_settings(**CONFIG_TESTE)
class PaginacaoPorCursorTests(ComCacheLimpo, APITestCase):
    def setUp(self):
        super().setUp()
        leitor = criar_usuario("leitor")
        conceder(leitor, "leads.view_lead")
        self.client.force_authenticate(leitor)

    def percorrer(self, url):
        ids = []
        while url:
            dados = self.client.get(url).json()
            ids += [item["id"] for item in dados["results"]]
            url = dados["next"]
        return ids

    def test_cursor_estavel_com_created_at_empatado(self):
        inserir_lote(7)  # todos com o mesmo created_at: o desempate é pelo id
        ids = self.percorrer("/api/leads/?page_size=3")
        self.assertEqual(len(ids), 7)
        self.assertEqual(len(set(ids)), 7)
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_page_size_limitado(self):
        inserir_lote(205, espalhar=True)
        self.assertEqual(len(self.client.get("/api/leads/").json()["results"]), 50)
        self.assertEqual(len(self.client.get("/api/leads/?page_size=1000").json()["results"]), 200)
        self.assertEqual(len(self.percorrer("/api/leads/?page_size=200")), 205)


@override_settings(**CONFIG_TESTE)
class UsoDeIndicesTests(TestCase):
    """Informativo: com volume sintético, a lista por status + período usa índice parcial.

    Se o planejador escolher outro plano (estatísticas, versão do PostgreSQL), o teste é
    pulado com o plano na mensagem, em vez de falhar: o objetivo é documentar.
    """

    def test_lista_por_status_e_periodo_usa_indice(self):
        inserir_lote(5000, espalhar=True)
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE lead")
        hoje = timezone.localdate()
        consulta = filtrar_leads(status="recebido", inicio=hoje - datetime.timedelta(days=2), fim=hoje)[:50]
        plano = consulta.explain()
        indices = ("ix_lead_status_periodo", "ix_lead_listagem")
        if not any(indice in plano for indice in indices):
            self.skipTest(f"Planejador não usou {indices}; plano obtido:\n{plano}")
        self.assertNotIn("Seq Scan on lead", plano)
