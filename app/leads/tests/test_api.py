"""API: captação pública e consulta/reclassificação de leads."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from ..models import Lead, LeadClassificacao, LeadStatusHistorico, Sistema
from ..services import anonimizar_lead
from .base import (
    ComCacheLimpo,
    basic,
    CAPTURA_VALIDA,
    conceder,
    CONFIG_TESTE,
    criar_lead,
    criar_usuario,
    FORM_VALIDO,
    URL_CAPTURA,
)


@override_settings(**CONFIG_TESTE)
class CapturaAPITests(ComCacheLimpo, APITestCase):
    def post(self, dados, **extra):
        return self.client.post(URL_CAPTURA, dados, format="json", **extra)

    def test_novo_duplicado_e_bot_respondem_igual(self):
        respostas = [
            self.post(CAPTURA_VALIDA),
            self.post({**CAPTURA_VALIDA, "email": " ANA.SOUZA@empresa.com", "nome": "Outra"}),
            self.post({**CAPTURA_VALIDA, "email": "bot@spam.com", "website": "http://spam"}),
        ]
        self.assertEqual({r.status_code for r in respostas}, {202})
        self.assertEqual(len({r.content for r in respostas}), 1)
        self.assertEqual(Lead.objects.get().nome, "Ana Souza")

    def test_consultas_do_endpoint_publico(self):
        self.post(CAPTURA_VALIDA)
        with self.assertNumQueries(3):  # SAVEPOINT, INSERT ... ON CONFLICT, RELEASE
            self.post({**CAPTURA_VALIDA, "email": "ana.souza@empresa.com"})

    def test_payload_invalido(self):
        resp = self.post({**CAPTURA_VALIDA, "email": "x", "telefone": "123"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(set(resp.json()), {"email", "telefone"})
        casos = [
            {**CAPTURA_VALIDA, "nome": "a" * 151},
            {**CAPTURA_VALIDA, "nome": {"$gt": ""}},
            {**CAPTURA_VALIDA, "empresa": ["Acme"]},
            {**CAPTURA_VALIDA, "aceite_politica": False},
            {**CAPTURA_VALIDA, "cargo": "   "},
            {**CAPTURA_VALIDA, "nome": "Ana\x00Souza"},
            {k: v for k, v in CAPTURA_VALIDA.items() if k != "aceite_politica"},
            ["nao", "e", "objeto"],
        ]
        for dados in casos:
            with self.subTest(dados=str(dados)[:60]):
                self.assertEqual(self.post(dados).status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_corpo_grande_demais_e_formato_errado(self):
        resp = self.post({**CAPTURA_VALIDA, "extra": "x" * 20000})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.client.post(URL_CAPTURA, CAPTURA_VALIDA).status_code, 415)  # multipart
        self.assertFalse(Lead.objects.exists())

    def test_campos_de_controle_injetados_sao_ignorados(self):
        resp = self.post({**CAPTURA_VALIDA, "status_atual": "enviado_crm", "classificacao_atual": "quente",
                          "deleted_at": "2020-01-01T00:00:00Z", "id": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(resp.status_code, 202)
        lead = Lead.objects.get()
        self.assertEqual((lead.status_atual, lead.classificacao_atual, lead.deleted_at), ("recebido", None, None))
        self.assertNotEqual(str(lead.pk), "00000000-0000-0000-0000-000000000000")

    def test_sem_sessao_nem_csrf(self):
        cliente = APIClient(enforce_csrf_checks=True)
        cliente.force_login(criar_usuario())
        self.assertEqual(cliente.post(URL_CAPTURA, CAPTURA_VALIDA, format="json").status_code, 202)

    @override_settings(LEADS_PUBLICO_TAXA="2/min")
    def test_rate_limit(self):
        codigos = [self.post({**CAPTURA_VALIDA, "email": f"a{i}@exemplo.com"}).status_code for i in range(3)]
        self.assertEqual(codigos, [202, 202, 429])
        # X-Forwarded-For não é confiável sem LEADS_PROXY_SALTOS: não contorna o limite.
        self.assertEqual(self.post(CAPTURA_VALIDA, HTTP_X_FORWARDED_FOR="10.9.9.9").status_code, 429)
        self.assertEqual(Lead.objects.count(), 2)

    @override_settings(LEADS_PUBLICO_TAXA="1/min", LEADS_PROXY_SALTOS=1, LEADS_PROXY_CONFIAVEIS=["127.0.0.0/8"])
    def test_ip_atras_de_proxy(self):
        self.assertEqual(self.post(CAPTURA_VALIDA, HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2").status_code, 202)
        self.assertEqual(self.post(CAPTURA_VALIDA, HTTP_X_FORWARDED_FOR="9.9.9.9, 2.2.2.2").status_code, 429)
        self.assertEqual(self.post(CAPTURA_VALIDA, HTTP_X_FORWARDED_FOR="3.3.3.3").status_code, 202)


@override_settings(**CONFIG_TESTE)
class LeadAPITests(ComCacheLimpo, APITestCase):
    def setUp(self):
        super().setUp()
        self.ana = criar_lead()
        self.bruno = criar_lead(nome="Bruno", email="bruno@beta.com", empresa="Beta")
        n8n = Sistema.por_codigo(Sistema.N8N)
        LeadClassificacao.objects.create(lead=self.ana, classificacao="quente", metodo="regra", sistema=n8n)
        LeadStatusHistorico.objects.create(lead=self.ana, status_novo="enviado_crm", sistema=n8n)
        self.leitor = criar_usuario("leitor")
        conceder(self.leitor, "leads.view_lead")
        self.lista = "/api/leads/"
        self.detalhe = f"/api/leads/{self.ana.pk}/"

    def entrar(self, usuario=None):
        self.client.force_authenticate(usuario or self.leitor)

    def test_anonimo_401_sem_permissao_403(self):
        for url in (self.lista, self.detalhe, f"{self.detalhe}historico/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post(f"{self.detalhe}reclassificar/", {}).status_code, 401)
        self.entrar(criar_usuario("semperm"))
        for url in (self.lista, self.detalhe, f"{self.detalhe}historico/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_lista_filtros_e_formato(self):
        self.entrar()
        resp = self.client.get(self.lista, {"classificacao": "quente"})
        self.assertEqual(resp.status_code, 200)
        (item,) = resp.json()["results"]
        self.assertEqual((item["id"], item["status_atual"], item["status_display"]),
                         (str(self.ana.pk), "enviado_crm", "Enviado ao CRM"))
        self.assertNotIn("deleted_at", item)
        self.assertEqual([i["id"] for i in self.client.get(self.lista, {"classificacao": "sem"}).json()["results"]],
                         [str(self.bruno.pk)])
        self.assertEqual(self.client.get(self.lista, {"status": "x"}).status_code, 400)
        self.assertEqual(self.client.get(self.lista, {"inicio": "2026-02-01", "fim": "2026-01-01"}).status_code, 400)
        resp = self.client.get(self.lista, {"q": "'; DROP TABLE lead; --"})
        self.assertEqual((resp.status_code, resp.json()["results"]), (200, []))

    def test_detalhe_e_historico(self):
        self.entrar()
        detalhe = self.client.get(self.detalhe).json()
        self.assertEqual(detalhe["politica_versao"], "1.0")
        self.assertNotIn("anonimizado_em", detalhe)
        historico = self.client.get(f"{self.detalhe}historico/").json()
        self.assertEqual([h["status_novo"] for h in historico["status"]], ["enviado_crm", "recebido"])
        self.assertEqual(historico["status"][0]["autor"], "n8n")
        self.assertEqual(historico["classificacoes"][0]["metodo"], "regra")

    def test_uuid_malformado_e_excluido_404(self):
        self.entrar()
        conceder(self.leitor, "leads.reclassificar_lead")
        self.assertEqual(self.client.get("/api/leads/nao-e-uuid/").status_code, 404)
        self.assertEqual(self.client.get("/api/leads/00000000-0000-0000-0000-000000000000/").status_code, 404)
        self.bruno.anonimizar()
        base = f"/api/leads/{self.bruno.pk}/"
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.get(f"{base}historico/").status_code, 404)
        resp = self.client.post(f"{base}reclassificar/", {"classificacao": "frio", "justificativa": "x"}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_reclassificar_exige_permissao(self):
        self.entrar()
        url = f"{self.detalhe}reclassificar/"
        self.assertEqual(self.client.post(url, {"classificacao": "frio", "justificativa": "x"}, format="json").status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_reclassificar_autor_e_metodo_do_servidor(self):
        conceder(self.leitor, "leads.reclassificar_lead")
        outro = criar_usuario("outro")
        self.entrar()
        resp = self.client.post(f"{self.detalhe}reclassificar/", {
            "classificacao": "morno", "justificativa": "Pediu retorno", "metodo": "ia", "usuario": str(outro.pk),
            "sistema": 1, "confianca": "0.9", "classificado_em": "2000-01-01T00:00:00Z",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual((resp.json()["autor"], resp.json()["metodo"]), ("leitor", "manual"))
        registro = self.ana.classificacoes.first()
        self.assertEqual((registro.usuario, registro.confianca, registro.sistema), (self.leitor, None, None))
        self.assertGreater(registro.classificado_em.year, 2000)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.classificacao_atual, "morno")
        self.assertEqual(self.ana.classificacoes.count(), 2)

    def test_reclassificar_payload_invalido(self):
        conceder(self.leitor, "leads.reclassificar_lead")
        self.entrar()
        url = f"{self.detalhe}reclassificar/"
        for dados in ({"classificacao": "fervendo", "justificativa": "x"}, {"classificacao": "frio"},
                      {"classificacao": "frio", "justificativa": "   "},
                      {"classificacao": "frio", "justificativa": "x" * 2001}):
            with self.subTest(dados=str(dados)[:50]):
                self.assertEqual(self.client.post(url, dados, format="json").status_code, 400)

    def test_sem_escrita_de_brinde(self):
        conceder(self.leitor, "leads.add_lead", "leads.change_lead", "leads.delete_lead")
        self.entrar()
        self.assertEqual(self.client.post(self.lista, CAPTURA_VALIDA, format="json").status_code, 405)
        self.assertEqual(self.client.put(self.detalhe, {"nome": "X"}, format="json").status_code, 405)
        self.assertEqual(self.client.patch(self.detalhe, {"status_atual": "erro"}, format="json").status_code, 405)
        self.assertEqual(self.client.delete(self.detalhe).status_code, 405)
        self.ana.refresh_from_db()
        self.assertEqual((self.ana.nome, self.ana.status_atual), ("Ana Souza", "enviado_crm"))

    def test_usuario_inativo_ou_excluido(self):
        for alterar in (lambda u: setattr(u, "is_active", False), lambda u: u.excluir()):
            usuario = criar_usuario(f"inativo{get_user_model().objects.count()}")
            conceder(usuario, "leads.view_lead")
            alterar(usuario)
            usuario.save()
            cliente = APIClient()
            cliente.force_login(usuario)
            with self.subTest(usuario=usuario.username):
                self.assertIn(cliente.get(self.lista).status_code, (401, 403))
            self.assertEqual(APIClient().get(self.lista, HTTP_AUTHORIZATION=basic(usuario.username)).status_code, 401)

    def test_consultas_constantes(self):
        """Lista: mesmo número de consultas com 5 e 50 leads; histórico sem N+1."""
        contagens = []
        for total in (5, 50):
            for i in range(total - Lead.objects.ativos().count()):
                criar_lead(email=f"lote{i}-{total}@exemplo.com")
            self.entrar(get_user_model().objects.get(pk=self.leitor.pk))  # sem cache de permissões
            with CaptureQueriesContext(connection) as consultas:
                self.assertEqual(len(self.client.get(self.lista).json()["results"]), total)
            contagens.append(len(consultas))
        self.assertEqual(contagens[0], contagens[1])
        self.assertLessEqual(contagens[0], 3)  # permissões (usuário e grupos) + lista
        n8n = Sistema.por_codigo(Sistema.N8N)
        for status_novo in ("em_nutricao", "erro", "enviado_crm"):
            LeadStatusHistorico.objects.create(lead=self.ana, status_novo=status_novo, sistema=n8n)
        self.entrar(get_user_model().objects.get(pk=self.leitor.pk))
        with self.assertNumQueries(5):  # permissões (2) + lead + 2 históricos com select_related
            self.client.get(f"{self.detalhe}historico/")


@override_settings(**CONFIG_TESTE)
class ThrottleSemCacheTests(ComCacheLimpo, APITestCase):
    def test_falha_do_cache_nao_derruba_a_captacao(self):
        from redis.exceptions import ConnectionError as RedisConnectionError
        from unittest import mock

        with mock.patch("app.leads.throttles.cache.add", side_effect=RedisConnectionError("fora")), \
                self.assertLogs("gohub.leads", "WARNING") as logs:
            resp = self.client.post(URL_CAPTURA, CAPTURA_VALIDA, format="json")
        self.assertEqual(resp.status_code, 202)
        self.assertNotIn("127.0.0.1", logs.output[0])


def _ctype_c():
    with connection.cursor() as cursor:
        cursor.execute("SELECT datctype FROM pg_database WHERE datname = current_database()")
        return cursor.fetchone()[0] in ("C", "POSIX")


@override_settings(**CONFIG_TESTE)
class LogsSemDadosPessoaisTests(ComCacheLimpo, APITestCase):
    """Auditoria de ponta a ponta: só ids; nunca e-mail, telefone nem justificativa."""

    def test_fluxos_completos(self):
        operador = criar_usuario("operador")
        conceder(operador, "leads.view_lead", "leads.reclassificar_lead", "leads.anonimizar_lead")
        justificativa = "Cliente pediu retorno em março"
        with self.assertLogs("gohub.auditoria", "INFO") as logs, self.captureOnCommitCallbacks(execute=True):
            self.client.post(URL_CAPTURA, CAPTURA_VALIDA, format="json")
            self.client.post(URL_CAPTURA, CAPTURA_VALIDA, format="json")  # duplicado: nada registrado
            self.client.post(reverse("leads:formulario"), {**FORM_VALIDO, "email": "bia@exemplo.com"})
            lead = Lead.objects.get(email="ana.souza@empresa.com")
            self.client.force_authenticate(operador)
            self.client.post(f"/api/leads/{lead.pk}/reclassificar/",
                             {"classificacao": "quente", "justificativa": justificativa}, format="json")
            anonimizar_lead(lead=lead, usuario=operador)
        texto = "\n".join(logs.output)
        eventos = [linha.split("evento=")[1].split()[0] for linha in logs.output]
        self.assertEqual(eventos, ["lead_captado", "lead_captado", "lead_reclassificado", "lead_anonimizado"])
        for proibido in ("ana.souza", "bia@exemplo.com", "@empresa.com", "98765", "+5511", "Ana Souza", justificativa):
            self.assertNotIn(proibido, texto)

    def test_busca_com_unicode_e_curingas(self):
        leitor = criar_usuario("leitor")
        conceder(leitor, "leads.view_lead")
        criar_lead(nome="João Ação", empresa="Café_100%", email="joao@exemplo.com")
        self.client.force_authenticate(leitor)
        # "AÇÃO" não encontra "Ação" quando o banco usa LC_CTYPE=C (upper() só converte ASCII):
        # limitação do locale do banco, registrada no relatório; aqui só casos independentes dele.
        for termo, esperado in (("ação", 1), ("JOÃO", 0 if _ctype_c() else 1), ("joão", 1), ("_100%", 1),
                                ("%%", 0), ("__", 0), ("🙂🙂", 0), ("\x00\x00", 0)):
            with self.subTest(termo=termo):
                resp = self.client.get("/api/leads/", {"q": termo})
                self.assertIn(resp.status_code, (200, 400))
                if resp.status_code == 200:
                    self.assertEqual(len(resp.json()["results"]), esperado)


@override_settings(**CONFIG_TESTE)
class CorrecoesDaAuditoriaAPITests(ComCacheLimpo, APITestCase):
    def test_sem_permissao_recebe_403_antes_de_validar_filtro(self):
        self.client.force_authenticate(criar_usuario("semperm"))
        self.assertEqual(self.client.get("/api/leads/", {"status": "invalido-x"}).status_code, 403)

    def test_erro_do_banco_no_cadastro_vira_400(self):
        # validate_email aceita o que o CHECK ck_lead_email recusa? Simula a recusa do banco.
        with mock.patch("app.leads.services.connection.cursor", side_effect=IntegrityError("ck_lead_email")):
            resp = self.client.post(URL_CAPTURA, CAPTURA_VALIDA, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("ck_lead_email", resp.content.decode())
