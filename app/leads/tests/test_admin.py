"""Admin: somente leitura e anonimização LGPD."""

from django.test import TestCase, override_settings
from django.urls import reverse

from .base import CONFIG_TESTE, conceder, criar_lead, criar_usuario


@override_settings(**CONFIG_TESTE)
class AdminLeadTests(TestCase):
    def setUp(self):
        self.lead = criar_lead(nome="<script>alert(1)</script>")
        self.operador = criar_usuario()
        conceder(self.operador, "users.acessar_admin", "leads.view_lead", "leads.view_leadstatushistorico",
                 "leads.view_leadclassificacao")
        self.client.force_login(self.operador)
        self.lista = reverse("admin:leads_lead_changelist")

    def acao(self, **extra):
        return self.client.post(self.lista, {"action": "anonimizar", "_selected_action": [self.lead.pk], **extra})

    def test_lista_e_detalhe_somente_leitura_com_escape(self):
        resp = self.client.get(self.lista)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "<script>alert(1)</script>")
        self.assertContains(resp, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(resp, self.lead.email)  # e-mail só no detalhe
        detalhe = self.client.get(reverse("admin:leads_lead_change", args=[self.lead.pk]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertNotContains(detalhe, 'name="nome"')  # sem campos editáveis

    def test_sem_inclusao_alteracao_nem_exclusao(self):
        conceder(self.operador, "leads.add_lead", "leads.change_lead", "leads.delete_lead")
        self.assertEqual(self.client.get(reverse("admin:leads_lead_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:leads_lead_delete", args=[self.lead.pk])).status_code, 403)
        resp = self.client.post(reverse("admin:leads_lead_change", args=[self.lead.pk]), {"nome": "Outro"})
        self.assertEqual(resp.status_code, 403)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.nome, "<script>alert(1)</script>")

    def test_historicos_somente_leitura(self):
        url = reverse("admin:leads_leadstatushistorico_changelist")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:leads_leadstatushistorico_add")).status_code, 403)

    def test_anonimizar_exige_permissao(self):
        self.assertIsNone(self.client.get(self.lista).context["action_form"])  # nenhuma ação disponível
        self.acao(confirmar="sim")  # ação fora da lista: o admin a ignora
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.anonimizado_em)

    def test_anonimizar_com_confirmacao(self):
        conceder(self.operador, "leads.anonimizar_lead")
        confirmacao = self.acao()
        self.assertTemplateUsed(confirmacao, "admin/leads/lead/confirmar_anonimizacao.html")
        self.assertContains(confirmacao, "&lt;script&gt;")
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.anonimizado_em)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.acao(confirmar="sim")
        self.assertEqual(resp.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.nome, "ANONIMIZADO")
        self.assertEqual(self.client.get(self.lista).context["cl"].result_count, 0)

    def test_sem_acesso_ao_admin(self):
        usuario = criar_usuario("semacesso")
        conceder(usuario, "leads.view_lead")
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.lista).status_code, 302)
