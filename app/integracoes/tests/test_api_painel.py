"""API do painel: /api/integracoes/ e /api/eventos-integracao/ (sessão + permissões explícitas)."""

import uuid
from unittest import mock

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from app.leads.models import Sistema

from ..models import EventoIntegracao, LeadIntegracao, ResultadoEvento, StatusIntegracao
from .base import CONFIG_TESTE, anonimizar, conceder, criar_usuario, integracao_n8n, novo_lead, sistema

URL_INTEGRACOES = "/api/integracoes/"
URL_EVENTOS = "/api/eventos-integracao/"


def criar_evento(lead, chave, **extra):
    dados = {
        "lead": lead, "sistema": sistema(Sistema.N8N), "tipo_evento": "crm.sincronizacao",
        "resultado": ResultadoEvento.ERRO, "chave_idempotencia": chave, "mensagem_erro": "CRM 503",
        "payload": {"execucao_id": "1"}, "ocorrido_em": timezone.now(),
    }
    dados.update(extra)
    evento = EventoIntegracao(**dados)
    evento.save()
    return evento


@override_settings(**CONFIG_TESTE)
class IntegracoesApiTests(APITestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.integracao = integracao_n8n(self.lead)
        self.usuario = criar_usuario()

    def entrar(self, *perms):
        conceder(self.usuario, *perms)
        self.client.force_authenticate(self.usuario)

    def test_anonimo_401(self):
        self.assertEqual(self.client.get(URL_INTEGRACOES).status_code, 401)
        self.assertEqual(self.client.post(f"{URL_INTEGRACOES}{self.integracao.pk}/reprocessar/").status_code, 401)

    def test_sem_permissao_403(self):
        self.entrar("leads.view_lead")
        self.assertEqual(self.client.get(URL_INTEGRACOES).status_code, 403)
        self.assertEqual(self.client.get(f"{URL_INTEGRACOES}{self.integracao.pk}/").status_code, 403)

    def test_lista_e_detalhe(self):
        self.entrar("integracoes.view_leadintegracao")
        resposta = self.client.get(URL_INTEGRACOES)
        self.assertEqual(resposta.status_code, 200)
        item = resposta.json()["results"][0]
        self.assertEqual(item["sistema"], "n8n")
        self.assertEqual(item["lead"], str(self.lead.pk))
        self.assertNotIn("nome", item)
        self.assertEqual(self.client.get(f"{URL_INTEGRACOES}{self.integracao.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"{URL_INTEGRACOES}abc/").status_code, 404)

    def test_filtros(self):
        self.entrar("integracoes.view_leadintegracao")
        outro = novo_lead(email="outro@empresa.com")
        LeadIntegracao.objects.filter(lead=outro).update(status=StatusIntegracao.SUCESSO, sucesso_em=timezone.now())
        resposta = self.client.get(URL_INTEGRACOES, {"status": "sucesso"})
        self.assertEqual([item["lead"] for item in resposta.json()["results"]], [str(outro.pk)])
        resposta = self.client.get(URL_INTEGRACOES, {"lead": str(self.lead.pk), "sistema": "n8n"})
        self.assertEqual(len(resposta.json()["results"]), 1)
        for invalido in ({"status": "x"}, {"sistema": "erp"}, {"lead": "nao-uuid"}, {"inicio": "ontem"}):
            self.assertEqual(self.client.get(URL_INTEGRACOES, invalido).status_code, 400, invalido)

    def test_lead_excluido_some(self):
        self.entrar("integracoes.view_leadintegracao")
        anonimizar(self.lead)
        self.assertEqual(self.client.get(URL_INTEGRACOES).json()["results"], [])
        self.assertEqual(self.client.get(f"{URL_INTEGRACOES}{self.integracao.pk}/").status_code, 404)

    def test_reprocessar_exige_permissao(self):
        self.entrar("integracoes.view_leadintegracao")
        url = f"{URL_INTEGRACOES}{self.integracao.pk}/reprocessar/"
        self.assertEqual(self.client.post(url).status_code, 403)
        conceder(self.usuario, "integracoes.reprocessar_integracao")
        self.usuario = type(self.usuario).objects.get(pk=self.usuario.pk)  # limpa o cache de permissões
        self.client.force_authenticate(self.usuario)
        self.assertEqual(self.client.get(url).status_code, 405)
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async"):
            resposta = self.client.post(url, {"status": "sucesso", "tentativas": 0}, format="json")
        self.assertEqual(resposta.status_code, 202)
        self.assertEqual(resposta.json()["status"], "pendente")  # campos enviados são ignorados

    def test_reprocessar_nada_a_fazer_409(self):
        self.entrar("integracoes.view_leadintegracao", "integracoes.reprocessar_integracao")
        LeadIntegracao.objects.filter(pk=self.integracao.pk).update(
            status=StatusIntegracao.SUCESSO, sucesso_em=timezone.now()
        )
        self.assertEqual(self.client.post(f"{URL_INTEGRACOES}{self.integracao.pk}/reprocessar/").status_code, 409)

    def test_sem_escrita(self):
        self.entrar("integracoes.view_leadintegracao", "integracoes.change_leadintegracao",
                    "integracoes.delete_leadintegracao", "integracoes.add_leadintegracao")
        url = f"{URL_INTEGRACOES}{self.integracao.pk}/"
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual(self.client.patch(url, {"status": "sucesso"}, format="json").status_code, 405)
        self.assertEqual(self.client.post(URL_INTEGRACOES, {}, format="json").status_code, 405)

    def test_paginacao_cursor_e_limite(self):
        self.entrar("integracoes.view_leadintegracao")
        for indice in range(4):
            novo_lead(email=f"l{indice}@empresa.com")
        ids = set()
        url, paginas = f"{URL_INTEGRACOES}?page_size=2", 0
        while url:
            dados = self.client.get(url).json()
            ids |= {item["id"] for item in dados["results"]}
            url, paginas = dados["next"], paginas + 1
        self.assertEqual((len(ids), paginas), (5, 3))  # empate de updated_at desfeito pelo id
        self.assertEqual(len(self.client.get(URL_INTEGRACOES, {"page_size": 1000}).json()["results"]), 5)

    def test_consultas_constantes(self):
        self.entrar("integracoes.view_leadintegracao")
        self.client.get(URL_INTEGRACOES)
        with self.assertNumQueries(1) as poucas:  # só a lista (permissões já em cache no usuário)
            self.client.get(URL_INTEGRACOES)
        for indice in range(45):
            novo_lead(email=f"x{indice}@empresa.com")
        with self.assertNumQueries(len(poucas.captured_queries)):
            self.assertEqual(len(self.client.get(URL_INTEGRACOES).json()["results"]), 46)


@override_settings(**CONFIG_TESTE)
class EventosApiTests(APITestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.evento = criar_evento(self.lead, "exec-001:no")
        self.usuario = criar_usuario()

    def test_permissoes(self):
        self.assertEqual(self.client.get(URL_EVENTOS).status_code, 401)
        conceder(self.usuario, "integracoes.view_leadintegracao")
        self.client.force_authenticate(self.usuario)
        self.assertEqual(self.client.get(URL_EVENTOS).status_code, 403)

    def test_lista_sem_payload_e_detalhe_com(self):
        conceder(self.usuario, "integracoes.view_eventointegracao")
        self.client.force_authenticate(self.usuario)
        item = self.client.get(URL_EVENTOS).json()["results"][0]
        self.assertNotIn("payload", item)
        self.assertNotIn("mensagem_erro", item)
        detalhe = self.client.get(f"{URL_EVENTOS}{self.evento.pk}/").json()
        self.assertEqual(detalhe["payload"], {"execucao_id": "1"})
        self.assertEqual(detalhe["mensagem_erro"], "CRM 503")

    def test_filtros_e_validacao(self):
        conceder(self.usuario, "integracoes.view_eventointegracao")
        self.client.force_authenticate(self.usuario)
        criar_evento(self.lead, "exec-002:no", resultado=ResultadoEvento.SUCESSO, mensagem_erro=None,
                     tipo_evento="classificacao.concluida")
        self.assertEqual(len(self.client.get(URL_EVENTOS, {"resultado": "sucesso"}).json()["results"]), 1)
        self.assertEqual(len(self.client.get(URL_EVENTOS, {"tipo_evento": "crm.sincronizacao"}).json()["results"]), 1)
        hoje = timezone.localdate().isoformat()
        self.assertEqual(len(self.client.get(URL_EVENTOS, {"inicio": hoje, "fim": hoje}).json()["results"]), 2)
        self.assertEqual(len(self.client.get(URL_EVENTOS, {"lead": str(uuid.uuid4())}).json()["results"]), 0)
        for invalido in ({"resultado": "x"}, {"tipo_evento": "outro.tipo"}, {"fim": "2020-01-01", "inicio": "2021-01-01"}):
            self.assertEqual(self.client.get(URL_EVENTOS, invalido).status_code, 400, invalido)

    def test_evento_de_lead_anonimizado_some(self):
        conceder(self.usuario, "integracoes.view_eventointegracao")
        self.client.force_authenticate(self.usuario)
        anonimizar(self.lead)
        self.assertEqual(self.client.get(URL_EVENTOS).json()["results"], [])
        self.assertEqual(self.client.get(f"{URL_EVENTOS}{self.evento.pk}/").status_code, 404)
