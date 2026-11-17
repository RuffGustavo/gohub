"""Correções da auditoria de segurança: throttle atômico, IPv6 /64, admin dos históricos,
validação da URL da política, proxies confiáveis, travas dos gatilhos e anonimização
do texto livre dos históricos."""

import os
import subprocess
import sys
import threading

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from ..forms import ReclassificacaoForm
from ..models import LeadClassificacao, LeadStatusHistorico, Sistema
from ..services import anonimizar_lead, reclassificar_lead
from ..throttles import captacao_permitida, identificador, ip_cliente
from ..validators import normalizar_justificativa
from .base import CONFIG_TESTE, conceder, criar_lead, criar_usuario


@override_settings(**{**CONFIG_TESTE, "LEADS_PUBLICO_TAXA": "5/min"})
class ThrottleAtomicoTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_requisicoes_paralelas_nao_estouram_o_limite(self):
        permitidas = []
        barreira = threading.Barrier(20)

        def tentar():
            requisicao = RequestFactory().post("/leads/novo/", REMOTE_ADDR="203.0.113.7")
            barreira.wait()
            permitidas.append(captacao_permitida(requisicao))

        threads = [threading.Thread(target=tentar) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(permitidas.count(True), 5)


    def test_ipv6_agrupado_por_64(self):
        self.assertEqual(identificador("2001:db8:1:2:aaaa::1"), identificador("2001:db8:1:2:ffff::9"))
        self.assertNotEqual(identificador("2001:db8:1:2::1"), identificador("2001:db8:1:3::1"))
        self.assertEqual(identificador("::ffff:198.51.100.1"), "198.51.100.1")
        self.assertEqual(identificador("198.51.100.1"), "198.51.100.1")
        self.assertEqual(identificador(""), "-")
        primeiro = RequestFactory().post("/", REMOTE_ADDR="2001:db8:1:2::1")
        for _ in range(5):
            self.assertTrue(captacao_permitida(primeiro))
        self.assertFalse(captacao_permitida(RequestFactory().post("/", REMOTE_ADDR="2001:db8:1:2::ffff")))


@override_settings(**CONFIG_TESTE)
class AdminHistoricosTests(TestCase):
    def setUp(self):
        self.lead = criar_lead(email="segredo@exemplo.com")
        self.staff = criar_usuario("staff")
        conceder(self.staff, "users.acessar_admin", "leads.view_leadstatushistorico", "leads.view_leadclassificacao")
        self.client.force_login(self.staff)
        self.url = reverse("admin:leads_leadstatushistorico_changelist")

    def test_historico_exige_view_lead(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        detalhe = reverse("admin:leads_leadstatushistorico_change", args=[self.lead.historico_status.get().pk])
        self.assertEqual(self.client.get(detalhe).status_code, 403)
        conceder(self.staff, "leads.view_lead")
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_busca_por_email_so_exata(self):
        conceder(self.staff, "leads.view_lead")
        contar = lambda termo: self.client.get(self.url, {"q": termo}).context["cl"].result_count  # noqa: E731
        self.assertEqual(contar("segredo@"), 0)
        self.assertEqual(contar("@exemplo.com"), 0)
        self.assertEqual(contar("segredo@exemplo.com"), 1)


def carregar_settings(ambiente_extra):
    """Importa setup.settings num processo separado com variáveis extras (sem tocar no atual)."""
    codigo = ("import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'setup.settings'); "
              "from django.conf import settings; settings.POLITICA_PRIVACIDADE_URL")
    return subprocess.run([sys.executable, "-c", codigo], env={**os.environ, **ambiente_extra},
                          capture_output=True, text=True, check=False)


class SettingsPoliticaTests(SimpleTestCase):
    def test_url_protocolo_relativo_recusada(self):
        resultado = carregar_settings({"POLITICA_PRIVACIDADE_URL": "//malicioso.example/politica"})
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("POLITICA_PRIVACIDADE_URL", resultado.stderr)


class GatilhosEndurecidosTests(TestCase):
    def test_funcoes_com_search_path_fixo(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT proname, proconfig FROM pg_proc WHERE proname IN ("
                "'fn_lead_normalizar', 'fn_lead_proteger_snapshot', 'fn_lead_proteger_anonimizado', "
                "'fn_lead_status_inicial', 'fn_exigir_lead_ativo', 'fn_status_hist_preparar', "
                "'fn_status_hist_aplicar', 'fn_classificacao_aplicar', 'fn_bloquear_alteracao', 'fn_anonimizar_lead')"
            )
            funcoes = dict(cursor.fetchall())
        self.assertEqual(len(funcoes), 10)
        for nome, config in funcoes.items():
            with self.subTest(nome):
                self.assertIn("search_path=public, pg_temp", config or [])
        self.assertTrue(Sistema.objects.filter(codigo=Sistema.APLICACAO_WEB).exists())


class AnonimizacaoDoTextoLivreTests(TestCase):
    """R3: o texto livre dos históricos é apagado na anonimização; o resto fica imutável."""

    def setUp(self):
        self.lead = criar_lead()
        self.usuario = criar_usuario()
        self.n8n = Sistema.por_codigo(Sistema.N8N)
        LeadStatusHistorico.objects.create(lead=self.lead, status_novo="erro", sistema=self.n8n,
                                           observacao="Falha ao enviar ao CRM")
        LeadClassificacao.objects.create(lead=self.lead, classificacao="frio", metodo="ia", sistema=self.n8n,
                                         confianca="0.5000", justificativa="Texto vindo do n8n")

    def sql(self, comando, params=None):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(comando, params or [])

    def test_anonimizacao_limpa_texto_livre_e_mantem_o_resto(self):
        reclassificar_lead(lead=self.lead, classificacao="quente", justificativa="Pediu proposta", usuario=self.usuario)
        anonimizar_lead(lead=self.lead, usuario=self.usuario)
        self.assertEqual(set(self.lead.classificacoes.values_list("justificativa", flat=True)), {None})
        self.assertEqual(set(self.lead.historico_status.values_list("observacao", flat=True)), {None})
        self.assertEqual(list(self.lead.historico_status.values_list("status_novo", flat=True)), ["erro", "recebido"])
        self.assertEqual(self.lead.classificacoes.count(), 2)
        self.assertEqual(self.lead.classificacao_atual, "quente")

    def test_excecao_da_imutabilidade_e_estreita(self):
        id_hist = self.lead.historico_status.get(status_novo="erro").pk
        # marcador ligado à mão, lead NÃO anonimizado: recusado
        with self.assertRaises(DatabaseError):
            self.sql("SELECT set_config('gohub.anonimizando', 'on', true); "
                     "UPDATE lead_status_historico SET observacao = NULL WHERE id = %s", [id_hist])
        self.lead.anonimizar()
        with connection.cursor() as cursor:  # fn_anonimizar_lead desliga o marcador ao terminar
            cursor.execute("SELECT coalesce(current_setting('gohub.anonimizando', true), '')")
            self.assertEqual(cursor.fetchone()[0], "off")
        for comando in (
            "UPDATE lead_status_historico SET observacao = 'outra' WHERE id = %s",     # não é NULL
            "UPDATE lead_status_historico SET status_novo = 'duplicado' WHERE id = %s",  # outra coluna
            "DELETE FROM lead_status_historico WHERE id = %s",
        ):
            with self.subTest(comando), self.assertRaises(DatabaseError):
                self.sql("SELECT set_config('gohub.anonimizando', 'on', true); " + comando, [id_hist])
        with self.assertRaises(DatabaseError):  # sem o marcador: recusado mesmo zerando
            self.sql("UPDATE lead_classificacao SET justificativa = NULL WHERE lead_id = %s", [self.lead.pk])

    def test_colunas_de_controle_protegidas(self):
        for comando in (
            "UPDATE lead SET deleted_at = now() WHERE id = %s",
            "UPDATE lead SET anonimizado_em = now(), deleted_at = now() WHERE id = %s",
            "UPDATE lead SET consentimento_em = now() - interval '1 day' WHERE id = %s",
            "UPDATE lead SET politica_versao = '9.9' WHERE id = %s",
            "UPDATE lead SET created_at = now() - interval '1 day' WHERE id = %s",
        ):
            with self.subTest(comando), self.assertRaises(IntegrityError):
                self.sql(comando, [self.lead.pk])

    def test_gatilho_futuro_nao_contorna_a_trava_do_snapshot(self):
        # Simula um gatilho novo (profundidade 2) que tenta mexer no snapshot sem o marcador.
        self.sql(
            "CREATE FUNCTION fn_teste_gatilho_intruso() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN UPDATE lead SET status_atual = 'duplicado' WHERE id = %s; RETURN NULL; END; $$; "
            "CREATE TRIGGER tg_teste_intruso AFTER UPDATE ON sistema FOR EACH ROW "
            "EXECUTE FUNCTION fn_teste_gatilho_intruso();".replace("%s", f"'{self.lead.pk}'")
        )
        with self.assertRaises(IntegrityError):
            self.sql("UPDATE sistema SET nome = nome WHERE codigo = 'crm'")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status_atual, "erro")


@override_settings(**CONFIG_TESTE)
class JustificativaSemDadosPessoaisTests(TestCase):
    def setUp(self):
        self.lead = criar_lead()
        self.usuario = criar_usuario()

    def test_padroes_recusados_e_texto_comum_aceito(self):
        recusados = ["Falar com ana@empresa.com", "CPF 123.456.789-09", "cpf 12345678909", "Ligar (11) 98765-4321",
                     "tel 11 3333-4444", "+55 11 987654321"]
        for texto in recusados:
            with self.subTest(texto), self.assertRaisesMessage(ValidationError, "dados pessoais"):
                normalizar_justificativa(texto)
        aceitos = ["Retorno em 17/11/2026 às 10:30", "Orçamento de R$ 15.000,00 para 2027",
                   "Pedido 2026-11-17, prioridade 3", "Sem interesse no momento"]
        for texto in aceitos:
            with self.subTest(texto):
                self.assertEqual(normalizar_justificativa(texto), texto)

    def test_validado_no_form_na_api_e_no_service(self):
        self.assertIn("justificativa", ReclassificacaoForm({"classificacao": "frio", "justificativa": "x@y.com"}).errors)
        with self.assertRaises(ValidationError):
            reclassificar_lead(lead=self.lead, classificacao="frio", justificativa="CPF 123.456.789-09",
                               usuario=self.usuario)
        conceder(self.usuario, "leads.view_lead", "leads.reclassificar_lead")
        api = APIClient()
        api.force_authenticate(self.usuario)
        resp = api.post(f"/api/leads/{self.lead.pk}/reclassificar/",
                        {"classificacao": "frio", "justificativa": "Ligar 11 98765-4321"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self.lead.classificacoes.exists())


@override_settings(**CONFIG_TESTE)
class ProxyConfiavelTests(SimpleTestCase):
    def ip(self, remoto, encaminhado):
        return ip_cliente(RequestFactory().post("/", REMOTE_ADDR=remoto, HTTP_X_FORWARDED_FOR=encaminhado))

    @override_settings(LEADS_PROXY_SALTOS=1, LEADS_PROXY_CONFIAVEIS=["172.18.0.0/16"])
    def test_xff_so_vindo_do_proxy(self):
        self.assertEqual(self.ip("172.18.0.5", "203.0.113.9"), "203.0.113.9")
        # outro container/host fora da rede do proxy: cabeçalho ignorado
        self.assertEqual(self.ip("10.0.0.7", "203.0.113.9"), "10.0.0.7")

    def test_settings_exigem_lista_com_saltos(self):
        for ambiente in ({"LEADS_PROXY_SALTOS": "1", "LEADS_PROXY_CONFIAVEIS": ""},
                         {"LEADS_PROXY_SALTOS": "1", "LEADS_PROXY_CONFIAVEIS": "nao-e-ip"}):
            with self.subTest(ambiente):
                resultado = carregar_settings(ambiente)
                self.assertNotEqual(resultado.returncode, 0)
                self.assertIn("LEADS_PROXY_CONFIAVEIS", resultado.stderr)
