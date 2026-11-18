"""Tasks Celery chamadas diretamente (sem worker nem broker)."""

from unittest import mock

from celery.exceptions import Retry
from django.test import TestCase, override_settings

from .. import tasks
from ..models import StatusIntegracao
from ..services import ResultadoEnvio
from .base import CONFIG_TESTE, integracao_n8n, novo_lead, patch_post, resposta_http


@override_settings(**CONFIG_TESTE)
class TasksTests(TestCase):
    def test_notificar_lead_recebido(self):
        lead = novo_lead()
        with patch_post(return_value=resposta_http(200)):
            tasks.notificar_lead_recebido.run(str(lead.pk))
        self.assertEqual(integracao_n8n(lead).status, StatusIntegracao.SUCESSO)

    def test_configuracao_das_tasks(self):
        self.assertTrue(tasks.notificar_lead_recebido.acks_late)
        self.assertEqual(tasks.notificar_lead_recebido.time_limit, 30)
        self.assertFalse(getattr(tasks.notificar_lead_recebido, "autoretry_for", ()))

    def test_periodicas(self):
        with mock.patch("app.integracoes.services.reprocessar_pendentes") as reprocessar:
            tasks.reprocessar_integracoes_pendentes.run()
        reprocessar.assert_called_once_with()
        with mock.patch("app.integracoes.services.liberar_travadas") as liberar:
            tasks.liberar_tentativas_travadas.run()
        liberar.assert_called_once_with()

    def test_aviso_anonimizacao_retenta_so_transitorio(self):
        transitorio = ResultadoEnvio(False, 503, "http_503", True, 1)
        definitivo = ResultadoEnvio(False, 401, "http_401", False, 1)
        alvo = "app.integracoes.services.executar_aviso_anonimizacao"
        with mock.patch(alvo, return_value=transitorio), self.assertRaises(Retry):
            tasks.notificar_lead_anonimizado.apply(args=["x"], throw=True).get()
        with mock.patch(alvo, return_value=definitivo), self.assertLogs("gohub.integracoes", "ERROR") as logs:
            tasks.notificar_lead_anonimizado.apply(args=["x"]).get()
        self.assertIn("evento=anonimizacao_nao_avisada", logs.output[0])
