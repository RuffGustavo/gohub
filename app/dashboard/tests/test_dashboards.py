"""Controle de acesso e integridade dos números dos dashboards (telas e API)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from app.leads.services import anonimizar_lead
from app.leads.tests.base import SENHA
from app.users.models import RegistroAcesso

from .base import acesso, conceder, configuracao, criar_usuario, ha, lead_em, mudar_status

URL_PESSOAL = "/api/dashboard/pessoal/"
URL_GERENCIAL = "/api/dashboard/gerencial/"


# ------------------------------------------------------------------ pessoal
@configuracao
class PessoalApiTests(APITestCase):
    def setUp(self):
        self.ana = criar_usuario("ana")
        self.bia = criar_usuario("bia", email="bia@exemplo.com")
        acesso(self.ana, quando=ha(dias=1), ip="203.0.113.1", navegador="Firefox")
        acesso(self.ana, quando=ha(dias=2), ip="203.0.113.1")
        acesso(self.ana, quando=ha(dias=40), ip="203.0.113.2")  # fora dos 30 dias, dentro do anterior
        acesso(self.ana, quando=ha(dias=3), status="falha", ip="198.51.100.99")
        for dias in range(5):
            acesso(self.bia, quando=ha(dias=dias), ip="192.0.2.50")

    def test_anonimo_401(self):
        self.assertEqual(self.client.get(URL_PESSOAL).status_code, 401)

    def test_numeros_so_do_proprio_usuario(self):
        self.client.force_authenticate(self.ana)
        dados = self.client.get(URL_PESSOAL).json()
        indicadores = dados["indicadores"]
        self.assertEqual(indicadores["acessos_total"], 3)
        self.assertEqual(indicadores["acessos_periodo"], 2)
        self.assertEqual(indicadores["acessos_periodo_anterior"], 1)
        self.assertEqual(indicadores["variacao_acessos"], 100.0)
        self.assertEqual(indicadores["falhas_periodo"], 1)
        self.assertEqual(indicadores["ips_distintos_periodo"], 1)
        self.assertEqual(sum(dados["graficos"]["evolucao"]["series"][0]["valores"]), 2)
        self.assertEqual(sorted(dados["graficos"]["navegadores"]["rotulos"]), ["Chrome", "Firefox"])
        self.assertEqual([ip["ip"] for ip in dados["ips_recentes"]], ["203.0.113.1"])
        self.assertNotIn("192.0.2.50", str(dados))

    def test_parametros_de_outro_usuario_sao_ignorados(self):
        self.client.force_authenticate(self.ana)
        for parametro in ("usuario", "usuario_id", "user", "id"):
            with self.subTest(parametro=parametro):
                dados = self.client.get(URL_PESSOAL, {parametro: str(self.bia.pk)}).json()
                self.assertEqual(dados["indicadores"]["acessos_total"], 3)
                self.assertNotIn("192.0.2.50", str(dados))

    def test_periodo_invalido_400(self):
        self.client.force_authenticate(self.ana)
        resposta = self.client.get(URL_PESSOAL, {"periodo": "personalizado", "inicio": "2026-01-10", "fim": "2026-01-01"})
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("fim", resposta.json())

    def test_sem_registros_responde_zero(self):
        self.client.force_authenticate(criar_usuario("novo", email="novo@exemplo.com"))
        dados = self.client.get(URL_PESSOAL).json()
        self.assertEqual(dados["indicadores"]["acessos_total"], 0)
        self.assertIsNone(dados["indicadores"]["variacao_acessos"])
        self.assertEqual(dados["graficos"]["navegadores"], {"rotulos": [], "valores": []})

    def test_sem_cache(self):
        self.client.force_authenticate(self.ana)
        self.assertIn("no-store", self.client.get(URL_PESSOAL)["Cache-Control"])

    def test_somente_get(self):
        self.client.force_authenticate(self.ana)
        self.assertEqual(self.client.post(URL_PESSOAL, {}).status_code, 405)


@configuracao
class PessoalTelaTests(TestCase):
    def setUp(self):
        self.ana = criar_usuario("ana")
        self.bia = criar_usuario("bia", email="bia@exemplo.com", nome="Beatriz Lima")
        acesso(self.bia, ip="192.0.2.50")
        acesso(self.ana, ip="203.0.113.1", quando=ha(horas=2))

    def test_anonimo_vai_para_o_login(self):
        resposta = self.client.get(reverse("users:painel"))
        self.assertEqual(resposta.status_code, 302)

    def test_historico_so_da_propria_conta_e_sem_dados_ficticios(self):
        self.client.login(username="ana", password=SENHA)
        resposta = self.client.get(reverse("users:painel"), {"usuario": str(self.bia.pk)})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "203.0.113.1")
        self.assertNotContains(resposta, "192.0.2.50")
        self.assertNotContains(resposta, "Beatriz Lima")
        self.assertNotContains(resposta, "Dados de exemplo")
        self.assertContains(resposta, 'data-gohub-dashboard="/api/dashboard/pessoal/"')
        self.assertContains(resposta, "assets/js/gohub/paginas/painel.js")
        self.assertIn("no-store", resposta["Cache-Control"])

    def test_filtro_invalido_avisa_e_usa_padrao(self):
        self.client.force_login(self.ana)
        resposta = self.client.get(reverse("users:painel"), {"periodo": "x", "status": "<script>"})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Alguns filtros são inválidos")
        self.assertNotContains(resposta, "<script>", html=False)

    def test_periodo_repassado_para_a_api(self):
        self.client.force_login(self.ana)
        resposta = self.client.get(reverse("users:painel"), {"periodo": "7d", "status": "falha"})
        self.assertContains(resposta, 'data-gohub-dashboard="/api/dashboard/pessoal/?periodo=7d"')

    def test_atalho_gerencial_so_com_permissao(self):
        self.client.force_login(self.ana)
        self.assertNotContains(self.client.get(reverse("users:painel")), reverse("dashboard:gerencial"))
        conceder(self.ana, "leads.view_lead")
        self.assertContains(self.client.get(reverse("users:painel")), reverse("dashboard:gerencial"))


# ------------------------------------------------------------------ gerencial
@configuracao
class GerencialPermissaoTests(APITestCase):
    def setUp(self):
        self.usuario = criar_usuario("gestor")

    def test_anonimo(self):
        self.assertEqual(self.client.get(URL_GERENCIAL).status_code, 401)
        self.assertEqual(self.client.get(reverse("dashboard:gerencial")).status_code, 302)

    def test_sem_permissao_403(self):
        self.client.force_authenticate(self.usuario)
        self.assertEqual(self.client.get(URL_GERENCIAL).status_code, 403)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("dashboard:gerencial")).status_code, 403)

    def test_permissao_de_escrita_nao_basta(self):
        conceder(self.usuario, "leads.change_lead", "users.change_usuario")
        self.client.force_authenticate(self.usuario)
        self.assertEqual(self.client.get(URL_GERENCIAL).status_code, 403)

    def test_cada_secao_exige_sua_permissao(self):
        casos = [
            ("leads.view_lead", ["leads"]),
            ("users.view_usuario", ["usuarios"]),
            ("users.view_registroacesso", ["acessos"]),
        ]
        for perm, esperado in casos:
            with self.subTest(perm=perm):
                usuario = criar_usuario(perm.replace(".", "_")[:40], email=f"{perm}@exemplo.com")
                conceder(usuario, perm)
                self.client.force_authenticate(usuario)
                dados = self.client.get(URL_GERENCIAL).json()
                self.assertEqual(dados["secoes"], esperado)
                for secao in {"leads", "usuarios", "acessos"} - set(esperado):
                    self.assertNotIn(secao, dados)

    def test_tela_sem_permissao_de_acessos_nao_mostra_ips(self):
        outro = criar_usuario("outro", email="outro@exemplo.com")
        acesso(outro, ip="192.0.2.77")
        conceder(self.usuario, "leads.view_lead")
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("dashboard:gerencial"))
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "192.0.2.77")
        self.assertNotContains(resposta, "Histórico geral de acessos")
        self.assertContains(resposta, "Leads recebidos no período")

    def test_tela_com_permissao_de_acessos_mostra_historico_geral(self):
        outro = criar_usuario("outro", email="outro@exemplo.com")
        acesso(outro, ip="192.0.2.77")
        acesso(outro, ip="192.0.2.78", status="falha")
        conceder(self.usuario, "users.view_registroacesso")
        self.client.force_login(self.usuario)
        url = reverse("dashboard:gerencial")
        self.assertContains(self.client.get(url), "192.0.2.77")
        filtrado = self.client.get(url, {"acesso-status": "falha"})
        self.assertContains(filtrado, "192.0.2.78")
        self.assertNotContains(filtrado, "192.0.2.77")
        self.assertNotContains(filtrado, "Leads recebidos no período")

    def test_superusuario_ve_tudo(self):
        raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.client.force_authenticate(raiz)
        self.assertEqual(self.client.get(URL_GERENCIAL).json()["secoes"], ["leads", "usuarios", "acessos"])


@configuracao
class GerencialNumerosTests(APITestCase):
    def setUp(self):
        self.gestor = criar_usuario("gestor")
        conceder(self.gestor, "leads.view_lead", "users.view_usuario", "users.view_registroacesso")
        self.client.force_authenticate(self.gestor)

    def leads(self, **params):
        return self.client.get(URL_GERENCIAL, params).json()["leads"]

    def test_banco_vazio_tudo_zero(self):
        dados = self.leads()
        self.assertEqual(dados["indicadores"]["total"], 0)
        self.assertEqual(dados["indicadores"]["taxa_conversao"], 0.0)
        self.assertIsNone(dados["indicadores"]["variacao_recebidos"])
        self.assertEqual(sum(dados["graficos"]["evolucao"]["series"][0]["valores"]), 0)
        self.assertFalse(dados["origem"]["disponivel"])
        self.assertFalse(dados["responsavel"]["disponivel"])

    def test_status_funil_e_conversao(self):
        a = lead_em(ha(dias=1), "a@exemplo.com")
        b = lead_em(ha(dias=2), "b@exemplo.com")
        c = lead_em(ha(dias=3), "c@exemplo.com")
        lead_em(ha(dias=4), "d@exemplo.com")
        lead_em(ha(dias=45), "antigo@exemplo.com")  # período anterior
        mudar_status(a, "em_nutricao")
        mudar_status(a, "enviado_crm")
        mudar_status(b, "enviado_crm")
        mudar_status(b, "erro")  # chegou ao CRM antes do erro: conta como convertido uma vez
        mudar_status(c, "duplicado")
        dados = self.leads()
        ind = dados["indicadores"]
        self.assertEqual(ind["total"], 5)
        self.assertEqual(ind["recebidos_periodo"], 4)
        self.assertEqual(ind["recebidos_periodo_anterior"], 1)
        self.assertEqual(ind["variacao_recebidos"], 300.0)
        self.assertEqual(ind["status_recebido"], 1)
        self.assertEqual(ind["status_enviado_crm"], 1)
        self.assertEqual(ind["status_erro"], 1)
        self.assertEqual(ind["status_duplicado"], 1)
        self.assertEqual(ind["convertidos_periodo"], 2)
        self.assertEqual(ind["taxa_conversao"], 50.0)
        self.assertEqual(ind["descartados_periodo"], 2)
        self.assertEqual(dados["graficos"]["funil"]["valores"], [4, 1, 2])
        self.assertEqual(sum(dados["graficos"]["status"]["valores"]), 4)  # cada lead uma vez
        self.assertEqual(sum(dados["graficos"]["desempenho_mensal"]["recebidos"]), 5)
        self.assertEqual(sum(dados["graficos"]["desempenho_mensal"]["convertidos"]), 2)

    def test_lead_anonimizado_fica_de_fora(self):
        lead = lead_em(ha(dias=1), "a@exemplo.com")
        anonimizar_lead(lead=lead, usuario=self.gestor)
        self.assertEqual(self.leads()["indicadores"]["total"], 0)

    def test_filtro_de_periodo_muda_os_numeros(self):
        lead_em(ha(dias=1), "a@exemplo.com")
        lead_em(ha(dias=20), "b@exemplo.com")
        self.assertEqual(self.leads(periodo="7d")["indicadores"]["recebidos_periodo"], 1)
        self.assertEqual(self.leads(periodo="30d")["indicadores"]["recebidos_periodo"], 2)

    def test_usuarios_e_acessos(self):
        bia = criar_usuario("bia", email="bia@exemplo.com")
        inativo = criar_usuario("inativo", email="inativo@exemplo.com", is_active=False)
        excluido = criar_usuario("excluido", email="excluido@exemplo.com")
        excluido.excluir()
        acesso(bia, quando=ha(dias=1))
        acesso(bia, quando=ha(dias=1, horas=1))
        acesso(self.gestor, quando=ha(dias=2))
        acesso(inativo, quando=ha(dias=2), status="falha")
        dados = self.client.get(URL_GERENCIAL).json()
        self.assertEqual(dados["usuarios"]["indicadores"], {"total": 3, "ativos": 2, "inativos": 1})
        acessos = dados["acessos"]["indicadores"]
        self.assertEqual(acessos["acessos_periodo"], 3)
        self.assertEqual(acessos["falhas_periodo"], 1)
        self.assertEqual(acessos["usuarios_com_acesso_periodo"], 2)
        series = dados["acessos"]["graficos"]["atividade"]["series"]
        self.assertEqual(sum(series[0]["valores"]), 3)

    def test_tabela_de_leads_com_filtros_e_paginacao(self):
        for indice in range(25):
            lead_em(ha(dias=1), f"lead{indice}@exemplo.com")
        self.client.force_login(self.gestor)
        resposta = self.client.get(reverse("dashboard:gerencial"), {"periodo": "7d"})
        self.assertContains(resposta, "25 lead(s) encontrado(s)")
        self.assertContains(resposta, "pagina_leads=2")
        filtrado = self.client.get(reverse("dashboard:gerencial"), {"periodo": "7d", "status": "enviado_crm"})
        self.assertContains(filtrado, "0 lead(s) encontrado(s)")

    def test_numero_de_consultas_nao_cresce_com_os_dados(self):
        for indice in range(10):
            lead_em(ha(dias=indice), f"q{indice}@exemplo.com")
            acesso(self.gestor, quando=ha(dias=indice))
        self.client.get(URL_GERENCIAL)  # aquece caches de permissão/content types
        with self.assertNumQueries(9):
            self.client.get(URL_GERENCIAL)


@configuracao
class SwaggerTests(TestCase):
    def test_schema_gera_com_as_rotas_do_dashboard(self):
        self.client.force_login(get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA))
        resposta = self.client.get("/swagger/?format=openapi")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("/dashboard/pessoal/", resposta.content.decode())


@configuracao
class RegistroNaoVazaPelaApiDeUsuariosTests(APITestCase):
    def test_me_nao_expoe_acessos(self):
        ana = criar_usuario("ana")
        acesso(ana)
        self.client.force_authenticate(ana)
        dados = self.client.get("/api/usuarios/me/").json()
        self.assertNotIn("acessos", dados)
        self.assertEqual(RegistroAcesso.objects.count(), 1)
