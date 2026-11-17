"""Telas: formulário público e painel (acesso, renderização, acessibilidade, XSS)."""

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ..models import Lead, LeadStatusHistorico, Sistema
from ..services import reclassificar_lead
from .base import (
    ComCacheLimpo,
    CAPTURA_VALIDA,
    conceder,
    CONFIG_TESTE,
    criar_lead,
    criar_usuario,
    FORM_VALIDO,
    URL_CAPTURA,
    XSS,
    XSS_ESCAPADO,
)


@override_settings(**CONFIG_TESTE)
class TelasAcessoTests(ComCacheLimpo, TestCase):
    """Controle de acesso das telas (a renderização é testada na etapa 8)."""

    def setUp(self):
        super().setUp()
        self.lead = criar_lead()
        self.usuario = criar_usuario()
        self.lista = reverse("leads:painel-lista")
        self.detalhe = reverse("leads:painel-detalhe", args=[self.lead.pk])
        self.reclassificar = reverse("leads:painel-reclassificar", args=[self.lead.pk])

    def test_anonimo_redireciona_para_login(self):
        for url in (self.lista, self.detalhe):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertRedirects(resp, f"{reverse('users:login')}?next={url}", fetch_redirect_response=False)
        resp = self.client.post(self.reclassificar, {"classificacao": "frio", "justificativa": "x"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.lead.classificacoes.exists())

    def test_sem_permissao_403(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.lista).status_code, 403)
        self.assertEqual(self.client.get(self.detalhe).status_code, 403)
        conceder(self.usuario, "leads.view_lead")
        resp = self.client.post(self.reclassificar, {"classificacao": "frio", "justificativa": "x"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(self.lead.classificacoes.exists())

    def test_reclassificar_get_405_csrf_e_caminho_feliz(self):
        conceder(self.usuario, "leads.view_lead", "leads.reclassificar_lead")
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.reclassificar).status_code, 405)
        cliente_csrf = Client(enforce_csrf_checks=True)
        cliente_csrf.force_login(self.usuario)
        self.assertEqual(cliente_csrf.post(self.reclassificar, {"classificacao": "frio", "justificativa": "x"}).status_code, 403)
        resp = self.client.post(self.reclassificar, {"classificacao": "frio", "justificativa": "Sem orçamento",
                                                     "metodo": "ia"})
        self.assertRedirects(resp, self.detalhe, fetch_redirect_response=False)
        registro = self.lead.classificacoes.get()
        self.assertEqual((registro.metodo, registro.usuario), ("manual", self.usuario))

    def test_excluido_e_uuid_invalido_404(self):
        conceder(self.usuario, "leads.view_lead", "leads.reclassificar_lead")
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get("/painel/leads/nao-e-uuid/").status_code, 404)
        self.lead.anonimizar()
        self.assertEqual(self.client.get(self.detalhe).status_code, 404)
        self.assertEqual(self.client.post(self.reclassificar, {"classificacao": "frio", "justificativa": "x"}).status_code, 404)

    def test_usuario_inativo_perde_a_sessao(self):
        conceder(self.usuario, "leads.view_lead")
        self.client.force_login(self.usuario)
        self.usuario.excluir()
        self.assertEqual(self.client.get(self.lista).status_code, 302)

    def test_formulario_publico_mesma_resposta(self):
        url = reverse("leads:formulario")
        respostas = [
            self.client.post(url, {**FORM_VALIDO, "email": "nova@exemplo.com"}),
            self.client.post(url, {**FORM_VALIDO, "email": " NOVA@exemplo.com ", "nome": "Outra"}),
            self.client.post(url, {**FORM_VALIDO, "email": "bot@spam.com", "website": "spam"}),
            self.client.post(url, {"website": "spam"}),
        ]
        for resp in respostas:
            self.assertRedirects(resp, reverse("leads:obrigado"), fetch_redirect_response=False)
        self.assertEqual(len({(r.status_code, r["Location"], r.content) for r in respostas}), 1)
        self.assertEqual(Lead.objects.filter(email="nova@exemplo.com").get().nome, "Ana Souza")
        self.assertFalse(Lead.objects.filter(email="bot@spam.com").exists())

    def test_formulario_publico_exige_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        resp = cliente.post(reverse("leads:formulario"), {**FORM_VALIDO, "email": "c@exemplo.com"})
        self.assertEqual(resp.status_code, 403)


@override_settings(**CONFIG_TESTE)
class TelasPublicasTests(ComCacheLimpo, TestCase):
    def test_formulario_renderiza_acessivel(self):
        resp = self.client.get(reverse("leads:formulario"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "layouts/auth.html")
        for trecho in ('autocomplete="name"', 'type="email"', 'autocomplete="email"', 'type="tel"', 'autocomplete="tel"',
                       'autocomplete="organization"', 'autocomplete="organization-title"', 'name="csrfmiddlewaretoken"',
                       '<meta name="robots" content="noindex">'):
            self.assertContains(resp, trecho)
        self.assertContains(resp, 'tabindex="-1" autocomplete="off"')
        self.assertContains(resp, '<div class="visually-hidden" aria-hidden="true">')
        self.assertNotContains(resp, "Ler a política de privacidade")  # sem URL configurada (P8)

    @override_settings(POLITICA_PRIVACIDADE_URL="https://exemplo.com/privacidade")
    def test_link_da_politica(self):
        resp = self.client.get(reverse("leads:formulario"))
        self.assertContains(resp, 'href="https://exemplo.com/privacidade" target="_blank" rel="noopener noreferrer"')

    def test_erros_ligados_aos_campos_e_dados_escapados(self):
        resp = self.client.post(reverse("leads:formulario"), {**FORM_VALIDO, "nome": XSS, "email": "x", "aceite_politica": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'aria-invalid="true" aria-describedby="erro-id_email')
        self.assertContains(resp, 'id="erro-id_aceite_politica"')
        self.assertContains(resp, XSS_ESCAPADO)
        self.assertNotContains(resp, XSS)
        self.assertFalse(Lead.objects.exists())

    def test_obrigado(self):
        resp = self.client.get(reverse("leads:obrigado"))
        self.assertContains(resp, 'role="status"')
        self.assertContains(resp, "Recebemos seus dados")

    @override_settings(LEADS_PUBLICO_TAXA="2/min")
    def test_limite_compartilhado_entre_tela_e_api(self):
        url = reverse("leads:formulario")
        self.assertEqual(self.client.post(url, {**FORM_VALIDO, "email": "t1@exemplo.com"}).status_code, 302)
        self.assertEqual(self.client.post(URL_CAPTURA, {**CAPTURA_VALIDA, "email": "t2@exemplo.com"},
                                          content_type="application/json").status_code, 202)
        resp = self.client.post(url, {**FORM_VALIDO, "email": "t3@exemplo.com"})
        self.assertEqual(resp.status_code, 429)
        self.assertTemplateUsed(resp, "leads/limite.html")
        self.assertEqual(self.client.post(url, {"website": "spam"}).status_code, 429)  # bot também conta
        self.assertEqual(Lead.objects.count(), 2)


@override_settings(**CONFIG_TESTE)
class TelasPainelTests(ComCacheLimpo, TestCase):
    def setUp(self):
        super().setUp()
        self.lead = criar_lead(nome=XSS, empresa=XSS, cargo=XSS)
        self.usuario = criar_usuario()
        conceder(self.usuario, "leads.view_lead")
        self.client.force_login(self.usuario)
        self.lista = reverse("leads:painel-lista")
        self.detalhe = reverse("leads:painel-detalhe", args=[self.lead.pk])

    def test_lista_escapa_e_marca_menu(self):
        resp = self.client.get(self.lista)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, XSS)
        self.assertContains(resp, XSS_ESCAPADO)
        self.assertContains(resp, "<caption")
        self.assertContains(resp, '<th scope="col">Status</th>')
        self.assertContains(resp, "Não classificado")
        self.assertContains(resp, f'href="{self.lista}" class="nk-menu-link" aria-current="page"')

    def test_menu_oculto_sem_permissao(self):
        outro = criar_usuario("outro")
        self.client.force_login(outro)
        resp = self.client.get(reverse("users:painel"))
        self.assertNotContains(resp, reverse("leads:painel-lista"))

    def test_filtros_preservados_na_paginacao(self):
        for i in range(50):
            criar_lead(email=f"p{i}@exemplo.com")
        resp = self.client.get(self.lista, {"classificacao": "sem", "q": "ana"})
        self.assertEqual(len(resp.context["leads"]), 50)
        self.assertContains(resp, "?classificacao=sem&amp;q=ana&amp;page=2")
        self.assertEqual(len(self.client.get(self.lista, {"classificacao": "sem", "q": "ana", "page": 2}).context["leads"]), 1)

    def test_filtro_invalido_nao_quebra(self):
        resp = self.client.get(self.lista, {"status": "x", "inicio": "2026-13-45", "q": "a", "fim": "2026-01-01"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alguns filtros são inválidos")
        self.assertContains(resp, 'aria-invalid="true"')

    def test_consultas_da_lista_constantes(self):
        contagens = []
        for total in (5, 50):
            for i in range(total - Lead.objects.ativos().count()):
                criar_lead(email=f"c{i}-{total}@exemplo.com")
            with CaptureQueriesContext(connection) as consultas:
                self.client.get(self.lista)
            contagens.append(len(consultas))
        self.assertEqual(contagens[0], contagens[1])

    def test_detalhe_sem_form_sem_permissao(self):
        resp = self.client.get(self.detalhe)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, XSS)
        self.assertContains(resp, f"<title>Lead {XSS_ESCAPADO} | GoHub</title>", html=False)
        self.assertNotContains(resp, reverse("leads:painel-reclassificar", args=[self.lead.pk]))
        self.assertContains(resp, "Lead recebido pelo formulário público")

    def test_detalhe_com_reclassificacao_e_historicos_escapados(self):
        conceder(self.usuario, "leads.reclassificar_lead")
        n8n = Sistema.por_codigo(Sistema.N8N)
        for status_novo in ("em_nutricao", "erro", "enviado_crm"):
            LeadStatusHistorico.objects.create(lead=self.lead, status_novo=status_novo, sistema=n8n, observacao=XSS)
        reclassificar_lead(lead=self.lead, classificacao="quente", justificativa=XSS, usuario=self.usuario)
        with CaptureQueriesContext(connection) as poucos:
            self.client.get(self.detalhe)
        for _ in range(5):
            LeadStatusHistorico.objects.create(lead=self.lead, status_novo="erro", sistema=n8n)
        with CaptureQueriesContext(connection) as muitos:
            resp = self.client.get(self.detalhe)
        self.assertEqual(len(poucos), len(muitos))  # sem N+1 nos históricos
        self.assertContains(resp, reverse("leads:painel-reclassificar", args=[self.lead.pk]))
        self.assertContains(resp, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(resp, XSS)
        self.assertContains(resp, "Quente")

    def test_reclassificacao_invalida_reexibe_com_erro(self):
        conceder(self.usuario, "leads.reclassificar_lead")
        resp = self.client.post(reverse("leads:painel-reclassificar", args=[self.lead.pk]),
                                {"classificacao": "quente", "justificativa": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, 'id="erro-id_justificativa"', status_code=400)
        self.assertFalse(self.lead.classificacoes.exists())
