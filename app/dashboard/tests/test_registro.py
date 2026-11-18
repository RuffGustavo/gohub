"""Gravação dos acessos pelos sinais de autenticação (login pela tela, falha, API)."""

import base64
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from app.leads.tests.base import SENHA
from app.users.models import RegistroAcesso

from .base import SAFARI_IPHONE, configuracao, criar_usuario


@configuracao
class RegistroAcessoTests(TestCase):
    def setUp(self):
        self.usuario = criar_usuario("ana")

    def entrar(self, senha, username="ana"):
        return self.client.post(
            reverse("users:login"), {"username": username, "password": senha},
            HTTP_USER_AGENT=SAFARI_IPHONE, REMOTE_ADDR="198.51.100.7",
        )

    def test_login_grava_sucesso_resumido(self):
        self.entrar(SENHA)
        registro = RegistroAcesso.objects.get()
        self.assertEqual(registro.usuario, self.usuario)
        self.assertEqual(
            (registro.status, registro.canal, registro.ip, registro.navegador, registro.sistema_operacional, registro.dispositivo),
            ("sucesso", "painel", "198.51.100.7", "Safari", "iOS", "celular"),
        )

    def test_senha_errada_grava_falha_da_conta(self):
        self.entrar("senha-errada")
        registro = RegistroAcesso.objects.get()
        self.assertEqual((registro.usuario, registro.status), (self.usuario, "falha"))

    def test_usuario_inexistente_nao_grava(self):
        self.entrar(SENHA, username="ninguem")
        self.assertFalse(RegistroAcesso.objects.exists())

    def test_ip_encaminhado_ignorado_sem_proxy_confiavel(self):
        self.client.post(
            reverse("users:login"), {"username": "ana", "password": SENHA},
            REMOTE_ADDR="198.51.100.7", HTTP_X_FORWARDED_FOR="1.2.3.4",
        )
        self.assertEqual(RegistroAcesso.objects.get().ip, "198.51.100.7")

    def test_erro_ao_gravar_nao_impede_o_login(self):
        with mock.patch.object(RegistroAcesso.objects, "create", side_effect=DatabaseError("x")):
            resposta = self.entrar(SENHA)
        self.assertRedirects(resposta, reverse("users:painel"), fetch_redirect_response=False)

    def test_registro_e_imutavel(self):
        self.entrar(SENHA)
        registro = RegistroAcesso.objects.get()
        registro.ip = "10.0.0.1"
        with self.assertRaises(ValidationError):
            registro.save()
        with self.assertRaises(ValidationError):
            RegistroAcesso.objects.update(ip="10.0.0.1")

    def test_admin_do_registro_e_somente_leitura(self):
        raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.entrar(SENHA)
        self.client.force_login(raiz)
        registro = RegistroAcesso.objects.filter(usuario=self.usuario).get()
        self.assertEqual(self.client.get(reverse("admin:users_registroacesso_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:users_registroacesso_add")).status_code, 403)
        url = reverse("admin:users_registroacesso_change", args=[registro.pk])
        self.client.post(url, {"ip": "10.0.0.1"})
        registro.refresh_from_db()
        self.assertEqual(registro.ip, "198.51.100.7")


@configuracao
class RegistroAcessoApiTests(APITestCase):
    def test_falha_de_basic_auth_fica_no_canal_api(self):
        usuario = criar_usuario("ana")
        self.client.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode(b"ana:errada").decode())
        self.assertEqual(self.client.get("/api/dashboard/pessoal/").status_code, 401)
        registro = RegistroAcesso.objects.get()
        self.assertEqual((registro.usuario, registro.status, registro.canal), (usuario, "falha", "api"))
