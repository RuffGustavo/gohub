"""Serviços: agendamento (I1/I2), envio do webhook, fila, reprocessamento e registro de eventos."""

import datetime
import json
import uuid
from decimal import Decimal
from unittest import mock

import requests
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from app.leads.models import LeadStatus, LeadStatusHistorico, Sistema

from .. import services
from ..assinatura import assinatura_valida
from ..models import EventoIntegracao, LeadIntegracao, ResultadoEvento, StatusIntegracao
from .base import (
    CONFIG_TESTE,
    INTEGRACOES_DESLIGADAS,
    SEGREDO,
    SEGREDOS,
    URL,
    anonimizar,
    criar_usuario,
    integracao_n8n,
    integracoes,
    novo_lead,
    patch_post,
    resposta_http,
    sistema,
)


def _vencer(integracao):
    LeadIntegracao.objects.filter(pk=integracao.pk).update(proxima_tentativa_em=timezone.now())


@override_settings(**CONFIG_TESTE)
class AgendamentoTests(TestCase):
    def test_lead_novo_cria_integracao_pendente_na_mesma_transacao(self):
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async") as enfileirar:
            with self.captureOnCommitCallbacks() as callbacks:
                lead = novo_lead()
                integracao = integracao_n8n(lead)  # já existe antes do commit (I1)
            self.assertEqual(integracao.status, StatusIntegracao.PENDENTE)
            self.assertEqual(integracao.tentativas, 0)
            self.assertIsNotNone(integracao.proxima_tentativa_em)
            enfileirar.assert_not_called()  # só depois do commit (I2)
            for callback in callbacks:
                callback()
        enfileirar.assert_called_once_with(args=[str(lead.pk)], retry=False)

    def test_duplicado_nao_cria_integracao(self):
        from app.leads.services import registrar_lead
        from app.leads.tests.base import DADOS_LEAD

        registrar_lead(**DADOS_LEAD)
        self.assertEqual(registrar_lead(**{**DADOS_LEAD, "nome": "Outra"}), (None, False))
        self.assertEqual(LeadIntegracao.objects.count(), 1)

    @override_settings(INTEGRACOES=INTEGRACOES_DESLIGADAS)
    def test_url_nao_configurada_nao_enfileira(self):
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async") as enfileirar:
            with self.captureOnCommitCallbacks(execute=True):
                lead = novo_lead()
        enfileirar.assert_not_called()
        self.assertEqual(integracao_n8n(lead).status, StatusIntegracao.PENDENTE)

    def test_broker_indisponivel_nao_quebra_o_cadastro(self):
        erro = OperationalError("broker fora")
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async", side_effect=erro):
            with self.assertLogs("gohub.integracoes", "WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    lead = novo_lead()
        self.assertIn("evento=fila_indisponivel", logs.output[0])
        self.assertEqual(integracao_n8n(lead).status, StatusIntegracao.PENDENTE)


@override_settings(**CONFIG_TESTE)
class EnvioTests(TestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.integracao = integracao_n8n(self.lead)

    def enviar(self, **kwargs):
        with patch_post(**kwargs) as post:
            resultado = services.executar_notificacao(str(self.lead.pk))
        return resultado, post

    def test_sucesso(self):
        resultado, post = self.enviar(return_value=resposta_http(204))
        self.assertTrue(resultado.ok)
        integracao = integracao_n8n(self.lead)
        self.assertEqual(integracao.status, StatusIntegracao.SUCESSO)
        self.assertEqual(integracao.tentativas, 1)
        self.assertIsNone(integracao.proxima_tentativa_em)
        self.assertIsNotNone(integracao.sucesso_em)
        evento = EventoIntegracao.objects.get()
        self.assertEqual((evento.tipo_evento, evento.resultado), ("lead.notificacao", ResultadoEvento.SUCESSO))
        self.assertEqual(evento.payload["tentativa"], 1)
        self.assertEqual(evento.payload["codigo_http"], 204)
        self.assertEqual(set(evento.payload), {"tentativa", "codigo_http", "duracao_ms"})
        self.assertTrue(evento.chave_idempotencia.startswith("saida:"))

    def test_corpo_cabecalhos_e_assinatura(self):
        _, post = self.enviar(return_value=resposta_http(200))
        args, kwargs = post.call_args
        self.assertEqual(args, (URL,))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (3, 5))
        self.assertNotIn("verify", kwargs)
        corpo = kwargs["data"]
        dados = json.loads(corpo)
        self.assertEqual(set(dados), {"evento", "lead_id", "ocorrido_em", "entrega_id"})  # I3
        self.assertEqual(dados["evento"], "lead.recebido")
        self.assertEqual(dados["lead_id"], str(self.lead.pk))
        self.assertEqual(dados["entrega_id"], str(uuid.uuid5(self.lead.pk, "lead.recebido")))
        cab = kwargs["headers"]
        self.assertEqual(cab["X-GoHub-Evento"], "lead.recebido")
        self.assertEqual(cab["X-GoHub-Entrega"], dados["entrega_id"])
        self.assertEqual(cab["User-Agent"], "GoHub-Integracoes/1")
        self.assertTrue(assinatura_valida(corpo, cab["X-GoHub-Timestamp"], cab["X-GoHub-Assinatura"], [SEGREDO]))
        for valor in ("Ana", "ana@empresa.com", "+5511987654321"):
            self.assertNotIn(valor, corpo.decode())

    def test_falhas_transitorias_agendam_backoff(self):
        casos = [
            {"side_effect": requests.exceptions.ConnectTimeout("https://n8n.exemplo.invalid")},
            {"side_effect": requests.exceptions.ConnectionError("x")},
            {"return_value": resposta_http(500)},
            {"return_value": resposta_http(429)},
        ]
        esperados = ["timeout", "conexao", "http_500", "http_429"]
        for tentativa, (kwargs, codigo) in enumerate(zip(casos, esperados), start=1):
            antes = timezone.now()
            resultado, _ = self.enviar(**kwargs)
            self.assertFalse(resultado.ok)
            integracao = integracao_n8n(self.lead)
            self.assertEqual(integracao.status, StatusIntegracao.ERRO)
            self.assertEqual(integracao.ultimo_erro, codigo)
            self.assertEqual(integracao.tentativas, tentativa)
            espera = integracao.proxima_tentativa_em - antes
            base = 60 * 2 ** (tentativa - 1)
            self.assertGreaterEqual(espera.total_seconds(), base * 0.9 - 1)
            self.assertLessEqual(espera.total_seconds(), base * 1.1 + 1)
            self.assertEqual(EventoIntegracao.objects.filter(mensagem_erro=codigo).count(), 1)
            _vencer(integracao)
        self.assertFalse(LeadStatusHistorico.objects.filter(lead=self.lead, status_novo=LeadStatus.ERRO).exists())

    def test_erro_definitivo_encerra_e_grava_historico(self):
        for status, codigo in ((401, "http_401"), (404, "http_404"), (302, "http_3xx:302")):
            with self.subTest(status=status):
                LeadIntegracao.objects.filter(pk=self.integracao.pk).update(
                    status=StatusIntegracao.PENDENTE, proxima_tentativa_em=timezone.now()
                )
                self.enviar(return_value=resposta_http(status))
                integracao = integracao_n8n(self.lead)
                self.assertEqual(integracao.status, StatusIntegracao.ERRO)
                self.assertEqual(integracao.ultimo_erro, codigo)
                self.assertIsNone(integracao.proxima_tentativa_em)
        # P9: um único histórico "erro" mesmo com três encerramentos.
        historicos = LeadStatusHistorico.objects.filter(lead=self.lead, status_novo=LeadStatus.ERRO)
        self.assertEqual(historicos.count(), 1)
        historico = historicos.get()
        self.assertEqual(historico.sistema.codigo, Sistema.APLICACAO_WEB)
        self.assertEqual(historico.observacao, "Falha ao notificar o n8n")
        self.assertEqual(historico.evento.tipo_evento, "lead.notificacao")

    @override_settings(INTEGRACOES=integracoes(INTEGRACOES_MAX_TENTATIVAS="2"))
    def test_esgotamento(self):
        self.enviar(return_value=resposta_http(503))
        self.assertIsNotNone(integracao_n8n(self.lead).proxima_tentativa_em)
        _vencer(self.integracao)
        self.enviar(return_value=resposta_http(503))
        integracao = integracao_n8n(self.lead)
        self.assertEqual((integracao.status, integracao.tentativas), (StatusIntegracao.ERRO, 2))
        self.assertIsNone(integracao.proxima_tentativa_em)
        self.assertEqual(LeadStatusHistorico.objects.filter(lead=self.lead, status_novo=LeadStatus.ERRO).count(), 1)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status_atual, LeadStatus.ERRO)
        # Reprocessamento manual acima do limite: uma tentativa, sem novo histórico.
        services.reprocessar_integracao(integracao=integracao, usuario=criar_usuario())
        self.enviar(return_value=resposta_http(503))
        integracao = integracao_n8n(self.lead)
        self.assertEqual(integracao.tentativas, 3)
        self.assertIsNone(integracao.proxima_tentativa_em)
        self.assertEqual(LeadStatusHistorico.objects.filter(lead=self.lead, status_novo=LeadStatus.ERRO).count(), 1)

    def test_sem_efeito_quando_nao_ha_o_que_fazer(self):
        # Próxima tentativa no futuro.
        LeadIntegracao.objects.filter(pk=self.integracao.pk).update(
            proxima_tentativa_em=timezone.now() + datetime.timedelta(minutes=5)
        )
        resultado, post = self.enviar(return_value=resposta_http(200))
        self.assertIsNone(resultado)
        post.assert_not_called()
        # Sucesso já registrado.
        _vencer(self.integracao)
        self.enviar(return_value=resposta_http(200))
        resultado, post = self.enviar(return_value=resposta_http(200))
        self.assertIsNone(resultado)
        post.assert_not_called()
        self.assertEqual(EventoIntegracao.objects.count(), 1)

    @override_settings(INTEGRACOES=INTEGRACOES_DESLIGADAS)
    def test_url_nao_configurada_mantem_pendente(self):
        resultado, post = self.enviar(return_value=resposta_http(200))
        self.assertIsNone(resultado)
        post.assert_not_called()
        integracao = integracao_n8n(self.lead)
        self.assertEqual((integracao.status, integracao.tentativas), (StatusIntegracao.PENDENTE, 0))

    def test_sistema_inativo_mantem_pendente(self):
        Sistema.objects.filter(codigo=Sistema.N8N).update(ativo=False)
        resultado, post = self.enviar(return_value=resposta_http(200))
        self.assertIsNone(resultado)
        post.assert_not_called()
        self.assertEqual(integracao_n8n(self.lead).tentativas, 0)

    def test_lead_anonimizado_nao_envia(self):
        anonimizar(self.lead)
        resultado, post = self.enviar(return_value=resposta_http(200))
        self.assertIsNone(resultado)
        post.assert_not_called()

    def test_id_invalido(self):
        self.assertIsNone(services.executar_notificacao("nao-e-uuid"))

    def test_erro_inesperado_grava_erro_e_relanca(self):
        with self.assertLogs("gohub.integracoes", "ERROR"), self.assertRaises(ValueError):
            self.enviar(side_effect=ValueError("bug"))
        integracao = integracao_n8n(self.lead)
        self.assertEqual((integracao.status, integracao.ultimo_erro), (StatusIntegracao.ERRO, "falha_envio"))
        self.assertIsNotNone(integracao.proxima_tentativa_em)

    def test_logs_sem_url_nem_dados_pessoais(self):
        with self.assertLogs("gohub.integracoes", "DEBUG") as logs:
            self.enviar(side_effect=requests.exceptions.SSLError(f"falha em {URL}"))
        texto = "\n".join(logs.output)
        self.assertIn("evento=webhook_tls_falhou", texto)
        for proibido in (*SEGREDOS, "ana@empresa.com", "+5511987654321", "Ana Souza"):
            self.assertNotIn(proibido, texto)
        self.assertEqual(EventoIntegracao.objects.get().mensagem_erro, "tls")


@override_settings(**CONFIG_TESTE)
class FilaTests(TestCase):
    def test_reprocessamento_respeita_fila_lote_e_lead_ativo(self):
        vencido, futuro, sucesso, anonimizado = (novo_lead(email=f"l{i}@empresa.com") for i in range(4))
        LeadIntegracao.objects.filter(lead=futuro).update(proxima_tentativa_em=timezone.now() + datetime.timedelta(hours=1))
        LeadIntegracao.objects.filter(lead=sucesso).update(
            status=StatusIntegracao.SUCESSO, sucesso_em=timezone.now(), proxima_tentativa_em=None
        )
        anonimizar(anonimizado)
        self.assertEqual(services.leads_para_reprocessar(), [vencido.pk])
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async") as enfileirar:
            self.assertEqual(services.reprocessar_pendentes(), 1)
        enfileirar.assert_called_once_with(args=[str(vencido.pk)], retry=False)
        extra = novo_lead(email="extra@empresa.com")
        self.assertEqual(len(services.leads_para_reprocessar(limite=1)), 1)
        self.assertIn(extra.pk, services.leads_para_reprocessar(limite=10))

    def test_reprocessamento_sem_url_ou_sistema_inativo(self):
        novo_lead()
        with override_settings(INTEGRACOES=INTEGRACOES_DESLIGADAS):
            self.assertEqual(services.leads_para_reprocessar(), [])
        Sistema.objects.filter(codigo=Sistema.N8N).update(ativo=False)
        self.assertEqual(services.leads_para_reprocessar(), [])

    def test_liberar_travadas(self):
        lead = novo_lead()
        LeadIntegracao.objects.filter(lead=lead).update(status=StatusIntegracao.EM_ANDAMENTO)
        self.assertEqual(services.liberar_travadas(), 0)  # recente
        depois = timezone.now() + datetime.timedelta(minutes=11)
        with self.assertLogs("gohub.integracoes", "WARNING"):
            self.assertEqual(services.liberar_travadas(agora=depois), 1)
        integracao = integracao_n8n(lead)
        self.assertEqual((integracao.status, integracao.ultimo_erro), (StatusIntegracao.ERRO, "interrompida"))
        self.assertEqual(integracao.proxima_tentativa_em, depois)


@override_settings(**CONFIG_TESTE)
class ReprocessamentoManualTests(TestCase):
    def setUp(self):
        self.usuario = criar_usuario()
        self.lead = novo_lead()
        self.integracao = integracao_n8n(self.lead)

    def test_erro_volta_para_a_fila_e_audita(self):
        LeadIntegracao.objects.filter(pk=self.integracao.pk).update(
            status=StatusIntegracao.ERRO, tentativas=5, ultima_tentativa_em=timezone.now(), proxima_tentativa_em=None
        )
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async") as enfileirar:
            with self.assertLogs("gohub.auditoria", "INFO") as logs, self.captureOnCommitCallbacks(execute=True):
                atual = services.reprocessar_integracao(integracao=self.integracao, usuario=self.usuario)
        self.assertEqual(atual.tentativas, 5)  # não zera
        self.assertIsNotNone(atual.proxima_tentativa_em)
        enfileirar.assert_called_once()
        self.assertIn("evento=integracao_reprocessada", logs.output[0])
        self.assertIn(f"usuario_id={self.usuario.pk}", logs.output[0])

    def test_nada_a_reprocessar(self):
        LeadIntegracao.objects.filter(pk=self.integracao.pk).update(
            status=StatusIntegracao.SUCESSO, sucesso_em=timezone.now()
        )
        with self.assertRaises(services.NadaAReprocessar):
            services.reprocessar_integracao(integracao=self.integracao, usuario=self.usuario)
        crm = LeadIntegracao.objects.create(lead=self.lead, sistema=sistema(Sistema.CRM), status=StatusIntegracao.ERRO)
        with self.assertRaises(services.NadaAReprocessar):
            services.reprocessar_integracao(integracao=crm, usuario=self.usuario)

    def test_lead_anonimizado(self):
        anonimizar(self.lead)
        with self.assertRaises(services.NadaAReprocessar):
            services.reprocessar_integracao(integracao=self.integracao, usuario=self.usuario)


@override_settings(**CONFIG_TESTE)
class SistemaTests(TestCase):
    def setUp(self):
        self.usuario = criar_usuario()

    def test_alternar(self):
        n8n = sistema(Sistema.N8N)
        with self.assertLogs("gohub.auditoria", "INFO") as logs, self.captureOnCommitCallbacks(execute=True):
            self.assertFalse(services.alternar_sistema(sistema=n8n, usuario=self.usuario).ativo)
        self.assertIn("evento=sistema_desativado", logs.output[0])
        self.assertTrue(services.alternar_sistema(sistema=n8n, usuario=self.usuario).ativo)

    def test_aplicacao_web_nao_desativa(self):
        with self.assertRaises(ValidationError):
            services.alternar_sistema(sistema=sistema(Sistema.APLICACAO_WEB), usuario=self.usuario)
        self.assertTrue(sistema(Sistema.APLICACAO_WEB).ativo)

    def test_testar_conexao(self):
        with patch_post(return_value=resposta_http(200)) as post, self.assertLogs("gohub.auditoria", "INFO") as logs:
            self.assertEqual(services.testar_conexao(sistema=sistema(Sistema.N8N), usuario=self.usuario), (True, "ok"))
        dados = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(set(dados), {"evento", "ocorrido_em"})
        self.assertIn("resultado=ok", logs.output[0])
        self.assertEqual(EventoIntegracao.objects.count(), 0)
        with patch_post(return_value=resposta_http(401)), self.assertLogs("gohub.auditoria", "INFO"):
            self.assertEqual(
                services.testar_conexao(sistema=sistema(Sistema.N8N), usuario=self.usuario), (False, "http_401")
            )
        with override_settings(INTEGRACOES=INTEGRACOES_DESLIGADAS), patch_post() as post:
            with self.assertLogs("gohub.auditoria", "INFO"):
                resultado = services.testar_conexao(sistema=sistema(Sistema.N8N), usuario=self.usuario)
            post.assert_not_called()
        self.assertEqual(resultado, (False, "nao_configurado"))
        with self.assertRaises(ValidationError):
            services.testar_conexao(sistema=sistema(Sistema.CRM), usuario=self.usuario)


@override_settings(**CONFIG_TESTE)
class AnonimizacaoTests(TestCase):
    def test_aviso_enfileirado_e_corpo(self):
        from app.leads.services import anonimizar_lead

        lead = novo_lead()
        LeadIntegracao.objects.create(lead=lead, sistema=sistema(Sistema.CRM), id_externo="crm-123")
        with mock.patch("app.integracoes.tasks.notificar_lead_anonimizado.apply_async") as enfileirar:
            with self.captureOnCommitCallbacks(execute=True):
                anonimizar_lead(lead=lead, usuario=criar_usuario())
        enfileirar.assert_called_once_with(args=[str(lead.pk)], retry=False)
        with patch_post(return_value=resposta_http(200)) as post:
            resultado = services.executar_aviso_anonimizacao(str(lead.pk))
        self.assertTrue(resultado.ok)
        dados = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(set(dados), {"evento", "lead_id", "ocorrido_em", "entrega_id", "ids_externos"})
        self.assertEqual(dados["ids_externos"], {"crm": "crm-123"})
        self.assertEqual(post.call_args.kwargs["headers"]["X-GoHub-Evento"], "lead.anonimizado")

    def test_lead_ativo_nao_avisa(self):
        lead = novo_lead()
        with patch_post() as post:
            self.assertIsNone(services.executar_aviso_anonimizacao(str(lead.pk)))
        post.assert_not_called()


@override_settings(**CONFIG_TESTE)
class RegistroEventoTests(TestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.n8n = sistema(Sistema.N8N)
        self.crm = sistema(Sistema.CRM)

    def dados(self, **extra):
        base = {
            "chave_idempotencia": "exec-1:no-crm",
            "tipo_evento": "crm.sincronizacao",
            "ocorrido_em": timezone.now(),
            "resultado": ResultadoEvento.SUCESSO,
            "sistema_destino": self.crm,
            "id_externo": "crm-1",
        }
        base.update(extra)
        return base

    def registrar(self, lead=None, **extra):
        return services.registrar_evento_recebido(sistema=self.n8n, lead=lead or self.lead, dados=self.dados(**extra))

    def test_cria_e_repete_sem_duplicar(self):
        evento, criado = self.registrar(status_novo=LeadStatus.ENVIADO_CRM)
        self.assertTrue(criado)
        self.assertIsNotNone(evento.pk)
        repetido, criado = self.registrar(status_novo=LeadStatus.ENVIADO_CRM)
        self.assertFalse(criado)
        self.assertEqual(repetido.pk, evento.pk)
        self.assertEqual(EventoIntegracao.objects.count(), 1)
        self.assertEqual(LeadIntegracao.objects.get(lead=self.lead, sistema=self.crm).tentativas, 1)
        self.assertEqual(self.lead.historico_status.filter(evento=evento).count(), 1)

    def test_mesma_chave_outro_lead(self):
        self.registrar()
        outro = novo_lead(email="outro@empresa.com")
        with self.assertRaises(services.ConflitoIdempotencia):
            self.registrar(lead=outro, id_externo="crm-2")

    def test_id_externo_em_uso(self):
        self.registrar()
        outro = novo_lead(email="outro@empresa.com")
        with self.assertRaises(services.ConflitoIdExterno):
            self.registrar(lead=outro, chave_idempotencia="exec-2:no-crm", status_novo=LeadStatus.ENVIADO_CRM)
        self.assertEqual(EventoIntegracao.objects.filter(lead=outro).count(), 0)
        self.assertFalse(outro.historico_status.exclude(status_novo=LeadStatus.RECEBIDO).exists())

    def test_ignorado_nao_altera_integracao(self):
        self.registrar(resultado=ResultadoEvento.IGNORADO, id_externo=None)
        self.assertFalse(LeadIntegracao.objects.filter(sistema=self.crm).exists())

    def test_evento_anterior_ao_sucesso_nao_altera(self):
        agora = timezone.now()
        self.registrar(ocorrido_em=agora)
        self.registrar(
            chave_idempotencia="exec-0:no-crm", ocorrido_em=agora - datetime.timedelta(minutes=1),
            resultado=ResultadoEvento.ERRO, mensagem_erro="CRM 503", id_externo=None,
        )
        integracao = LeadIntegracao.objects.get(sistema=self.crm)
        self.assertEqual((integracao.status, integracao.tentativas), (StatusIntegracao.SUCESSO, 1))
        self.assertEqual(EventoIntegracao.objects.count(), 2)
        self.registrar(
            chave_idempotencia="exec-3:no-crm", ocorrido_em=agora + datetime.timedelta(minutes=1),
            resultado=ResultadoEvento.ERRO, mensagem_erro="CRM 503", id_externo=None,
        )
        integracao.refresh_from_db()
        self.assertEqual((integracao.status, integracao.tentativas, integracao.ultimo_erro),
                         (StatusIntegracao.ERRO, 2, "CRM 503"))
        self.assertEqual(integracao.id_externo, "crm-1")

    def test_classificacao_vinculada_ao_evento(self):
        evento, _ = self.registrar(
            tipo_evento="classificacao.concluida", sistema_destino=None, id_externo=None,
            classificacao={"classificacao": "frio", "metodo": "ia", "confianca": Decimal("0.87")},
        )
        classificacao = self.lead.classificacoes.get()
        self.assertEqual((classificacao.evento_id, classificacao.sistema_id), (evento.pk, self.n8n.pk))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.classificacao_atual, "frio")
