"""Unitários: assinatura, sanitização, backoff, classificação de erros e configuração."""

import datetime

import requests
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from ..assinatura import JANELA_SEGUNDOS, assinar, assinatura_valida, timestamp_valido
from ..configuracao import carregar
from ..sanitizacao import sanitizar_texto
from ..services import calcular_backoff, classificar_excecao, classificar_status
from .base import ENV_TESTE, SEGREDO, SEGREDO_ANTERIOR, TOKEN


class AssinaturaTests(SimpleTestCase):
    corpo = b'{"evento":"lead.recebido"}'

    def test_mesmo_corpo_mesma_assinatura(self):
        self.assertEqual(assinar(self.corpo, "100", SEGREDO), assinar(self.corpo, "100", SEGREDO))
        self.assertRegex(assinar(self.corpo, "100", SEGREDO), r"^sha256=[0-9a-f]{64}$")

    def test_qualquer_diferenca_invalida(self):
        assinatura = assinar(self.corpo, "100", SEGREDO)
        self.assertTrue(assinatura_valida(self.corpo, "100", assinatura, [SEGREDO]))
        self.assertFalse(assinatura_valida(self.corpo + b" ", "100", assinatura, [SEGREDO]))
        self.assertFalse(assinatura_valida(self.corpo, "101", assinatura, [SEGREDO]))
        self.assertFalse(assinatura_valida(self.corpo, "100", assinatura, [SEGREDO_ANTERIOR]))

    def test_formato_obrigatorio(self):
        hexa = assinar(self.corpo, "100", SEGREDO).removeprefix("sha256=")
        for valor in (hexa, "sha1=" + hexa, "sha256=" + hexa.upper(), "", None, "sha256=" + hexa[:-1]):
            self.assertFalse(assinatura_valida(self.corpo, "100", valor, [SEGREDO]), valor)

    def test_segredo_anterior_aceito(self):
        assinatura = assinar(self.corpo, "100", SEGREDO_ANTERIOR)
        self.assertTrue(assinatura_valida(self.corpo, "100", assinatura, [SEGREDO, SEGREDO_ANTERIOR]))
        self.assertFalse(assinatura_valida(self.corpo, "100", assinatura, [SEGREDO, ""]))

    def test_janela_do_timestamp(self):
        agora = 1_800_000_000
        self.assertEqual(timestamp_valido(str(agora - JANELA_SEGUNDOS), agora), agora - JANELA_SEGUNDOS)
        self.assertEqual(timestamp_valido(str(agora + JANELA_SEGUNDOS), agora), agora + JANELA_SEGUNDOS)
        self.assertIsNone(timestamp_valido(str(agora - JANELA_SEGUNDOS - 1), agora))
        self.assertIsNone(timestamp_valido(str(agora + JANELA_SEGUNDOS + 1), agora))
        for valor in ("", "abc", "-1", "1.5", None, "1" * 13):
            self.assertIsNone(timestamp_valido(valor, agora))


class SanitizacaoTests(SimpleTestCase):
    def test_email_e_telefone(self):
        texto = sanitizar_texto("Falha p/ ana.souza@empresa.com tel +55 (11) 98765-4321 e 11987654321")
        self.assertEqual(texto, "Falha p/ [email] tel [telefone] e [telefone]")

    def test_cpf_vira_marcador(self):
        self.assertEqual(sanitizar_texto("cpf 123.456.789-00"), "cpf [telefone]")

    def test_quebras_e_controle(self):
        self.assertEqual(sanitizar_texto("linha 1\r\nlinha\t2\x00 fim"), "linha 1 linha 2 fim")

    def test_texto_limpo_intacto(self):
        self.assertEqual(sanitizar_texto("CRM respondeu 503 (timeout)"), "CRM respondeu 503 (timeout)")
        self.assertEqual(sanitizar_texto("pedido 1234567"), "pedido 1234567")  # 7 dígitos: não é telefone

    def test_limite_e_vazio(self):
        self.assertEqual(len(sanitizar_texto("a" * 1500)), 1000)
        self.assertIsNone(sanitizar_texto("   "))
        self.assertIsNone(sanitizar_texto(None))


class BackoffTests(SimpleTestCase):
    def test_valores_sem_jitter(self):
        esperado = {1: 60, 2: 120, 3: 240, 4: 480, 5: 960, 6: 1920, 7: 3600, 10: 3600}
        for tentativa, segundos in esperado.items():
            self.assertEqual(
                calcular_backoff(tentativa, aleatorio=lambda a, b: 1.0), datetime.timedelta(seconds=segundos)
            )

    def test_jitter_dentro_de_10_por_cento(self):
        for tentativa in range(1, 7):
            base = 60 * 2 ** (tentativa - 1)
            for _ in range(50):
                segundos = calcular_backoff(tentativa).total_seconds()
                self.assertGreaterEqual(segundos, base * 0.9)
                self.assertLessEqual(segundos, min(base * 1.1, 3600))

    def test_teto_com_jitter(self):
        self.assertEqual(calcular_backoff(8, aleatorio=lambda a, b: b).total_seconds(), 3600)


class ClassificacaoErrosTests(SimpleTestCase):
    def test_excecoes(self):
        casos = [
            (requests.exceptions.ConnectTimeout(), "timeout"),
            (requests.exceptions.ReadTimeout(), "timeout"),
            (requests.exceptions.SSLError(), "tls"),
            (requests.exceptions.ConnectionError(), "conexao"),
            (requests.exceptions.InvalidHeader(), "falha_envio"),
        ]
        for exc, codigo in casos:
            self.assertEqual(classificar_excecao(exc), (codigo, True))

    def test_status_http(self):
        casos = {
            200: (None, False), 204: (None, False),
            301: ("http_3xx:301", False), 302: ("http_3xx:302", False),
            408: ("http_408", True), 425: ("http_425", True), 429: ("http_429", True),
            400: ("http_400", False), 401: ("http_401", False), 403: ("http_403", False), 404: ("http_404", False),
            500: ("http_500", True), 503: ("http_503", True),
        }
        for status, esperado in casos.items():
            self.assertEqual(classificar_status(status), esperado, status)


class ConfiguracaoTests(SimpleTestCase):
    def assertErro(self, env, variavel):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            carregar(env)
        mensagem = str(ctx.exception)
        self.assertIn(variavel, mensagem)
        for valor in env.values():
            if len(valor) >= 8:
                self.assertNotIn(valor, mensagem)

    def test_padroes_desligados(self):
        cfg = carregar({})
        self.assertEqual(cfg["n8n"]["token"], "")
        self.assertEqual(cfg["n8n"]["webhook_url"], "")
        self.assertEqual((cfg["timeout_conexao"], cfg["timeout_leitura"], cfg["max_tentativas"]), (3, 5, 5))
        self.assertEqual((cfg["interno_taxa"], cfg["auth_falha_taxa"]), ("600/min", "20/min"))

    def test_configuracao_valida(self):
        cfg = carregar({**ENV_TESTE, "INTEGRACOES_N8N_REDES_PERMITIDAS": "10.0.0.5, 172.18.0.0/16"})
        self.assertEqual(cfg["n8n"]["redes_permitidas"], ["10.0.0.5/32", "172.18.0.0/16"])

    def test_token_curto(self):
        self.assertErro({"INTEGRACOES_N8N_TOKEN": "curto-demais-1234", "INTEGRACOES_N8N_SEGREDO_HMAC": SEGREDO},
                        "INTEGRACOES_N8N_TOKEN")

    def test_segredo_curto(self):
        self.assertErro({"INTEGRACOES_N8N_SEGREDO_HMAC": "curto-demais-1234"}, "INTEGRACOES_N8N_SEGREDO_HMAC")

    def test_http_sem_permissao(self):
        env = {"INTEGRACOES_N8N_WEBHOOK_URL": "http://n8n:5678/webhook/x", "INTEGRACOES_N8N_SEGREDO_HMAC": SEGREDO}
        self.assertErro(env, "INTEGRACOES_N8N_WEBHOOK_URL")
        cfg = carregar({**env, "INTEGRACOES_N8N_WEBHOOK_PERMITIR_HTTP": "true"})
        self.assertEqual(cfg["n8n"]["webhook_url"], "http://n8n:5678/webhook/x")

    def test_url_com_credencial(self):
        env = {"INTEGRACOES_N8N_WEBHOOK_URL": "https://usuario:senhasecreta@n8n.exemplo.invalid/x",
               "INTEGRACOES_N8N_SEGREDO_HMAC": SEGREDO}
        self.assertErro(env, "INTEGRACOES_N8N_WEBHOOK_URL")

    def test_url_invalida(self):
        for url in ("ftp://n8n/x", "https://", "n8n.exemplo.invalid/x", "https://h:porta/x", "https://h/x#frag"):
            self.assertErro({"INTEGRACOES_N8N_WEBHOOK_URL": url, "INTEGRACOES_N8N_SEGREDO_HMAC": SEGREDO},
                            "INTEGRACOES_N8N_WEBHOOK_URL")

    def test_segredo_ausente_com_url_ou_token(self):
        self.assertErro({"INTEGRACOES_N8N_WEBHOOK_URL": "https://n8n.exemplo.invalid/x"}, "INTEGRACOES_N8N_SEGREDO_HMAC")
        self.assertErro({"INTEGRACOES_N8N_TOKEN": TOKEN}, "INTEGRACOES_N8N_SEGREDO_HMAC")

    def test_cidr_invalido(self):
        self.assertErro({"INTEGRACOES_N8N_REDES_PERMITIDAS": "10.0.0.0/33"}, "INTEGRACOES_N8N_REDES_PERMITIDAS")

    def test_anteriores(self):
        self.assertErro({**ENV_TESTE, "INTEGRACOES_N8N_TOKEN_ANTERIOR": TOKEN}, "INTEGRACOES_N8N_TOKEN_ANTERIOR")
        self.assertErro({"INTEGRACOES_N8N_TOKEN_ANTERIOR": TOKEN, "INTEGRACOES_N8N_SEGREDO_HMAC": SEGREDO},
                        "INTEGRACOES_N8N_TOKEN_ANTERIOR")
        self.assertErro({**ENV_TESTE, "INTEGRACOES_N8N_SEGREDO_HMAC_ANTERIOR": SEGREDO},
                        "INTEGRACOES_N8N_SEGREDO_HMAC_ANTERIOR")

    def test_numeros_e_taxas(self):
        self.assertErro({"INTEGRACOES_TIMEOUT_CONEXAO": "0"}, "INTEGRACOES_TIMEOUT_CONEXAO")
        self.assertErro({"INTEGRACOES_TIMEOUT_LEITURA": "abc"}, "INTEGRACOES_TIMEOUT_LEITURA")
        self.assertErro({"INTEGRACOES_TIMEOUT_CONEXAO": "15", "INTEGRACOES_TIMEOUT_LEITURA": "15"},
                        "INTEGRACOES_TIMEOUT_LEITURA")
        self.assertErro({"INTEGRACOES_MAX_TENTATIVAS": "21"}, "INTEGRACOES_MAX_TENTATIVAS")
        self.assertErro({"INTEGRACOES_INTERNO_TAXA": "muito"}, "INTEGRACOES_INTERNO_TAXA")
        self.assertErro({"INTEGRACOES_AUTH_FALHA_TAXA": "0/min"}, "INTEGRACOES_AUTH_FALHA_TAXA")


class ChecksTests(SimpleTestCase):
    def test_w001_token_sem_rede(self):
        from django.test import override_settings

        from ..checks import rede_da_api_interna
        from .base import integracoes

        with override_settings(INTEGRACOES=integracoes()):
            self.assertEqual([aviso.id for aviso in rede_da_api_interna(None)], ["integracoes.W001"])
        with override_settings(INTEGRACOES=integracoes(INTEGRACOES_N8N_REDES_PERMITIDAS="10.0.0.5")):
            self.assertEqual(rede_da_api_interna(None), [])

    def test_w002_rede_sem_proxy_configurado(self):
        from django.test import override_settings

        from ..checks import rede_atras_de_proxy
        from .base import integracoes

        com_rede = integracoes(INTEGRACOES_N8N_REDES_PERMITIDAS="10.0.0.5")
        with override_settings(INTEGRACOES=com_rede, LEADS_PROXY_SALTOS=0):
            self.assertEqual([aviso.id for aviso in rede_atras_de_proxy(None)], ["integracoes.W002"])
        with override_settings(INTEGRACOES=com_rede, LEADS_PROXY_SALTOS=1):
            self.assertEqual(rede_atras_de_proxy(None), [])
        with override_settings(INTEGRACOES=integracoes(), LEADS_PROXY_SALTOS=0):
            self.assertEqual(rede_atras_de_proxy(None), [])
