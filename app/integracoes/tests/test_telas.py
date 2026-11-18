"""Telas do painel e admin: acesso, CSRF, métodos, redirecionamento seguro, XSS e consultas."""

from unittest import mock

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.leads.models import Sistema
from app.leads.tests.base import XSS, XSS_ESCAPADO

from ..models import EventoIntegracao, LeadIntegracao, ResultadoEvento, StatusIntegracao
from .base import CONFIG_TESTE, SEGREDOS, conceder, criar_usuario, integracao_n8n, novo_lead, patch_post, resposta_http, sistema

VER = ("integracoes.view_leadintegracao", "integracoes.view_eventointegracao")


def criar_evento(lead, chave="exec-001:no", **extra):
    dados = {
        "lead": lead, "sistema": sistema(Sistema.N8N), "tipo_evento": "crm.sincronizacao",
        "resultado": ResultadoEvento.ERRO, "chave_idempotencia": chave, "mensagem_erro": XSS,
        "payload": {"workflow": XSS, "tentativa": 1}, "ocorrido_em": timezone.now(), "id_externo": "CRM-1",
    }
    dados.update(extra)
    evento = EventoIntegracao(**dados)
    evento.save()
    return evento


@override_settings(**CONFIG_TESTE)
class AcessoTelasTests(TestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.integracao = integracao_n8n(self.lead)
        self.evento = criar_evento(self.lead)
        self.usuario = criar_usuario()
        self.n8n = sistema(Sistema.N8N)
        self.leituras = [
            reverse("integracoes:painel-visao-geral"),
            reverse("integracoes:painel-fila"),
            reverse("integracoes:painel-eventos"),
            reverse("integracoes:painel-evento-detalhe", args=[self.evento.pk]),
        ]
        self.acoes = [
            reverse("integracoes:painel-reprocessar", args=[self.integracao.pk]),
            reverse("integracoes:painel-sistema-alternar", args=[self.n8n.pk]),
            reverse("integracoes:painel-sistema-testar", args=[self.n8n.pk]),
        ]

    def test_anonimo_redireciona_para_login(self):
        for url in self.leituras + self.acoes:
            resposta = self.client.post(url) if url in self.acoes else self.client.get(url)
            self.assertEqual(resposta.status_code, 302, url)
            self.assertIn(reverse("users:login"), resposta["Location"])

    def test_sem_permissao_403(self):
        conceder(self.usuario, "leads.view_lead")
        self.client.force_login(self.usuario)
        for url in self.leituras:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for url in self.acoes:
            self.assertEqual(self.client.post(url).status_code, 403, url)

    def test_ver_sem_acoes(self):
        conceder(self.usuario, *VER)
        self.client.force_login(self.usuario)
        for url in self.leituras:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for url in self.acoes:
            self.assertEqual(self.client.post(url).status_code, 403, url)
        pagina = self.client.get(reverse("integracoes:painel-fila")).content.decode()
        self.assertNotIn("Reprocessar</", pagina)
        self.assertNotIn("Testar conexão", self.client.get(self.leituras[0]).content.decode())

    def test_get_em_acoes_405(self):
        conceder(self.usuario, *VER, "integracoes.reprocessar_integracao", "integracoes.gerenciar_integracoes")
        self.client.force_login(self.usuario)
        for url in self.acoes:
            self.assertEqual(self.client.get(url).status_code, 405, url)

    def test_post_sem_csrf_403(self):
        conceder(self.usuario, *VER, "integracoes.reprocessar_integracao", "integracoes.gerenciar_integracoes")
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)
        for url in self.acoes:
            self.assertEqual(cliente.post(url).status_code, 403, url)
        self.assertTrue(sistema(Sistema.N8N).ativo)

    def test_lead_excluido_404(self):
        from .base import anonimizar

        conceder(self.usuario, *VER, "integracoes.reprocessar_integracao")
        self.client.force_login(self.usuario)
        anonimizar(self.lead)
        self.assertEqual(self.client.get(self.leituras[3]).status_code, 404)
        self.assertEqual(self.client.post(self.acoes[0]).status_code, 404)
        self.assertNotIn(str(self.lead.pk)[:8], self.client.get(self.leituras[1] + "?status=pendente").content.decode())


@override_settings(**CONFIG_TESTE)
class AcoesTelasTests(TestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.integracao = integracao_n8n(self.lead)
        self.usuario = criar_usuario()
        conceder(self.usuario, *VER, "integracoes.reprocessar_integracao", "integracoes.gerenciar_integracoes")
        self.client.force_login(self.usuario)

    def test_reprocessar_com_next_seguro(self):
        url = reverse("integracoes:painel-reprocessar", args=[self.integracao.pk])
        LeadIntegracao.objects.filter(pk=self.integracao.pk).update(proxima_tentativa_em=None,
                                                                     status=StatusIntegracao.ERRO)
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async"):
            resposta = self.client.post(url, {"next": "/painel/integracoes/fila/?status=erro"})
        self.assertRedirects(resposta, "/painel/integracoes/fila/?status=erro", fetch_redirect_response=False)
        self.assertIsNotNone(integracao_n8n(self.lead).proxima_tentativa_em)
        for externo in ("https://externo.exemplo/", "//externo.exemplo/", "javascript:alert(1)"):
            resposta = self.client.post(url, {"next": externo})
            self.assertEqual(resposta["Location"], reverse("integracoes:painel-fila"), externo)

    def test_reprocessar_nada_a_fazer(self):
        crm = LeadIntegracao.objects.create(lead=self.lead, sistema=sistema(Sistema.CRM))
        resposta = self.client.post(reverse("integracoes:painel-reprocessar", args=[crm.pk]), follow=True)
        self.assertContains(resposta, "Nada a reprocessar")

    def test_alternar_e_aplicacao_web(self):
        n8n = sistema(Sistema.N8N)
        self.client.post(reverse("integracoes:painel-sistema-alternar", args=[n8n.pk]))
        self.assertFalse(sistema(Sistema.N8N).ativo)
        web = sistema(Sistema.APLICACAO_WEB)
        resposta = self.client.post(reverse("integracoes:painel-sistema-alternar", args=[web.pk]), follow=True)
        self.assertContains(resposta, "não pode ser desativada")
        self.assertTrue(sistema(Sistema.APLICACAO_WEB).ativo)

    def test_testar_conexao(self):
        n8n = sistema(Sistema.N8N)
        with patch_post(return_value=resposta_http(503)):
            resposta = self.client.post(reverse("integracoes:painel-sistema-testar", args=[n8n.pk]), follow=True)
        self.assertContains(resposta, "http_503")
        crm = sistema(Sistema.CRM)
        self.assertEqual(self.client.post(reverse("integracoes:painel-sistema-testar", args=[crm.pk])).status_code, 404)

    def test_testar_conexao_tem_limite_por_usuario(self):
        """M4: o envio é síncrono; acima de 5 por minuto o teste não é feito."""
        from django.core.cache import cache

        cache.clear()
        url = reverse("integracoes:painel-sistema-testar", args=[sistema(Sistema.N8N).pk])
        with patch_post(return_value=resposta_http(200)) as post:
            for _ in range(5):
                self.client.post(url)
            resposta = self.client.post(url, follow=True)
        self.assertEqual(post.call_count, 5)
        self.assertContains(resposta, "Muitos testes seguidos")

    def test_visao_geral_sem_valores_de_credenciais(self):
        pagina = self.client.get(reverse("integracoes:painel-visao-geral")).content.decode()
        self.assertIn("Configuradas", pagina)
        for segredo in SEGREDOS:
            self.assertNotIn(segredo, pagina)


@override_settings(**CONFIG_TESTE)
class ConteudoTelasTests(TestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.evento = criar_evento(self.lead)
        self.usuario = criar_usuario()
        conceder(self.usuario, *VER, "leads.view_lead")
        self.client.force_login(self.usuario)

    def test_xss_escapado_no_detalhe_e_no_lead(self):
        for url in (reverse("integracoes:painel-evento-detalhe", args=[self.evento.pk]),
                    reverse("leads:painel-detalhe", args=[self.lead.pk])):
            pagina = self.client.get(url).content.decode()
            self.assertNotIn(XSS, pagina)
            self.assertIn(XSS_ESCAPADO, pagina)

    def test_listas_sem_nome_do_lead(self):
        for url in (reverse("integracoes:painel-fila") + "?status=pendente", reverse("integracoes:painel-eventos")):
            pagina = self.client.get(url).content.decode()
            self.assertIn(str(self.lead.pk)[:8], pagina)
            self.assertNotIn("Ana Souza", pagina)
            self.assertNotIn("ana@empresa.com", pagina)

    def test_secao_do_lead_so_com_permissao(self):
        outro = criar_usuario("sem_integracoes")
        conceder(outro, "leads.view_lead")
        self.client.force_login(outro)
        pagina = self.client.get(reverse("leads:painel-detalhe", args=[self.lead.pk])).content.decode()
        self.assertNotIn('id="titulo-integracoes"', pagina)
        self.client.force_login(self.usuario)
        pagina = self.client.get(reverse("leads:painel-detalhe", args=[self.lead.pk])).content.decode()
        self.assertIn('id="titulo-integracoes"', pagina)
        self.assertIn("<title>Lead Ana Souza | GoHub</title>", pagina)
        self.assertEqual(pagina.count('id="titulo-integracoes"'), 1)

    def test_eventos_do_lead_exigem_ver_eventos(self):
        """M3: só view_leadintegracao mostra o estado por sistema, sem eventos nem mensagens."""
        so_estado = criar_usuario("so_estado")
        conceder(so_estado, "leads.view_lead", "integracoes.view_leadintegracao")
        self.client.force_login(so_estado)
        pagina = self.client.get(reverse("leads:painel-detalhe", args=[self.lead.pk])).content.decode()
        self.assertIn('id="titulo-integracoes"', pagina)
        self.assertNotIn("evento(s) de integração do lead", pagina)
        self.assertNotIn(XSS_ESCAPADO, pagina)

    def test_menu(self):
        pagina = self.client.get(reverse("integracoes:painel-fila")).content.decode()
        self.assertIn('href="/painel/integracoes/fila/" class="nk-menu-link" aria-current="page"', pagina)

    def test_filtros_invalidos_ignorados(self):
        resposta = self.client.get(reverse("integracoes:painel-fila"), {"status": "x", "lead": "nao-uuid"})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Alguns filtros são inválidos")

    def test_consultas_constantes(self):
        url_fila = reverse("integracoes:painel-fila") + "?status=pendente"
        url_eventos = reverse("integracoes:painel-eventos")
        url_lead = reverse("leads:painel-detalhe", args=[self.lead.pk])
        contagens = {}
        for url in (url_fila, url_eventos, url_lead, reverse("integracoes:painel-visao-geral")):
            self.client.get(url)
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(url)
            contagens[url] = len(ctx.captured_queries)
        for indice in range(8):
            lead = novo_lead(email=f"x{indice}@empresa.com")
            criar_evento(lead, chave=f"exec-{indice:03d}:mais")
            criar_evento(self.lead, chave=f"exec-{indice:03d}:lead")
        for url, total in contagens.items():
            with self.assertNumQueries(total):
                self.client.get(url)


@override_settings(**CONFIG_TESTE)
class AdminTests(TestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.integracao = integracao_n8n(self.lead)
        self.evento = criar_evento(self.lead)
        self.usuario = criar_usuario("admin_integracoes")
        conceder(self.usuario, "users.acessar_admin", *VER)
        self.client.force_login(self.usuario)

    def test_somente_leitura_e_sem_dados_pessoais(self):
        for modelo in ("eventointegracao", "leadintegracao"):
            lista = self.client.get(reverse(f"admin:integracoes_{modelo}_changelist"))
            self.assertEqual(lista.status_code, 200)
            self.assertNotContains(lista, "Ana Souza")
            self.assertEqual(self.client.get(reverse(f"admin:integracoes_{modelo}_add")).status_code, 403)
        detalhe = self.client.get(reverse("admin:integracoes_eventointegracao_change", args=[self.evento.pk]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertNotContains(detalhe, XSS)
        self.assertNotContains(detalhe, "Ana Souza")

    def test_busca(self):
        url = reverse("admin:integracoes_eventointegracao_changelist")
        link = reverse("admin:integracoes_eventointegracao_change", args=[self.evento.pk])
        self.assertContains(self.client.get(url, {"q": "exec-001:no"}), link)
        self.assertContains(self.client.get(url, {"q": str(self.lead.pk)}), link)
        self.assertNotContains(self.client.get(url, {"q": "ana@empresa.com"}), link)
        self.assertEqual(self.client.get(url, {"q": "qualquer texto"}).status_code, 200)

    def test_acao_reprocessar_exige_permissao(self):
        url = reverse("admin:integracoes_leadintegracao_changelist")
        self.assertNotContains(self.client.get(url), "Reprocessar notificação")
        conceder(self.usuario, "integracoes.reprocessar_integracao")
        LeadIntegracao.objects.filter(pk=self.integracao.pk).update(status=StatusIntegracao.ERRO)
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async"):
            resposta = self.client.post(
                url, {"action": "reprocessar", "_selected_action": [self.integracao.pk]}, follow=True
            )
        self.assertContains(resposta, "1 integração(ões) reagendada(s)")
