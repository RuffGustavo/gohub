from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from .forms import UsuarioCreationForm, UsuarioPasswordChangeForm

SENHA = "S3nha-forte-de-teste"


def criar_usuario(username="admin", **extra):
    dados = {"email": f"{username}@exemplo.com", "nome": "Ana Souza", "password": SENHA}
    dados.update(extra)
    return get_user_model().objects.create_user(username=username, **dados)


class UsuarioModelTests(TestCase):
    def test_senha_em_argon2id_e_dados_normalizados(self):
        usuario = criar_usuario(username=" Admin ", email="  ADMIN@Exemplo.COM ", nome="  Ana   Souza ")
        usuario.refresh_from_db()
        self.assertTrue(usuario.password.startswith("argon2$argon2id$"))
        self.assertEqual(usuario.username, "admin")
        self.assertEqual(usuario.email, "admin@exemplo.com")
        self.assertEqual(usuario.nome, "Ana Souza")
        self.assertTrue(usuario.check_password(SENHA))

    def test_create_user_exige_senha(self):
        with self.assertRaises(ValueError):
            get_user_model().objects.create_user(username="semsenha", email="s@exemplo.com", nome="Sem")

    def test_create_superuser(self):
        usuario = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.assertTrue(usuario.is_superuser)
        self.assertTrue(usuario.is_staff)

    def test_banco_recusa_senha_em_texto_puro(self):
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO usuario (nome, username, email, senha_hash) VALUES (%s, %s, %s, %s)",
                ["Xavier", "xavier", "x@exemplo.com", "senha123"],
            )

    def test_banco_recusa_username_fora_do_formato(self):
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO usuario (nome, username, email, senha_hash) VALUES (%s, %s, %s, %s)",
                ["Xavier", "x y", "x@exemplo.com", "$argon2id$v=19$abc"],
            )

    def test_banco_recusa_excluido_ativo(self):
        usuario = criar_usuario()
        with self.assertRaises(IntegrityError), transaction.atomic():
            get_user_model().objects.filter(pk=usuario.pk).update(deleted_at=timezone.now())

    def test_gatilhos_normalizam_e_atualizam_updated_at(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO usuario (nome, username, email, senha_hash) VALUES (%s, %s, %s, %s) "
                "RETURNING id, username, email, nome",
                ["  Bia   Lima ", " BIA ", " BIA@Exemplo.com ", "$argon2id$v=19$abc"],
            )
            _, username, email, nome = cursor.fetchone()
        self.assertEqual((username, email, nome), ("bia", "bia@exemplo.com", "Bia Lima"))

        usuario = criar_usuario()
        # O gatilho sobrescreve qualquer valor enviado para updated_at com now().
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE usuario SET nome = %s, updated_at = %s WHERE id = %s",
                ["Ana Maria", "2000-01-01T00:00:00Z", usuario.pk],
            )
        usuario.refresh_from_db()
        self.assertGreater(usuario.updated_at.year, 2000)

    def test_exclusao_logica_bloqueia_autenticacao(self):
        usuario = criar_usuario()
        usuario.excluir()
        usuario.refresh_from_db()
        self.assertFalse(usuario.is_active)
        self.assertIsNotNone(usuario.deleted_at)
        self.assertIsNone(authenticate(username="admin", password=SENHA))

    def test_inativo_nao_autentica(self):
        criar_usuario(is_active=False)
        self.assertIsNone(authenticate(username="admin", password=SENHA))

    def test_formulario_de_criacao_valida_unicidade_apos_normalizar(self):
        criar_usuario()
        form = UsuarioCreationForm(
            data={"username": " ADMIN ", "email": "outra@exemplo.com", "nome": "Outra",
                  "password1": SENHA, "password2": SENHA}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)
        self.assertNotIn("usable_password", form.fields)

    def test_formulario_de_criacao_recusa_username_invalido(self):
        form = UsuarioCreationForm(
            data={"username": "a b", "email": "a@exemplo.com", "nome": "Ana",
                  "password1": SENHA, "password2": SENHA}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_troca_de_senha_no_admin_nao_permite_senha_inutilizavel(self):
        usuario = criar_usuario()
        form = UsuarioPasswordChangeForm(usuario, data={"usable_password": "false"})
        self.assertNotIn("usable_password", form.fields)
        self.assertFalse(form.is_valid())


# O manifest do WhiteNoise só existe após collectstatic; para renderizar templates
# nos testes, usa o storage sem manifest (não afeta a configuração real).
STORAGES_DE_TESTE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_DE_TESTE)
class LoginLogoutTests(TestCase):
    def setUp(self):
        self.usuario = criar_usuario()
        self.url_login = reverse("users:login")

    def test_pagina_de_login(self):
        resposta = self.client.get(self.url_login)
        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, "users/login.html")
        self.assertContains(resposta, "csrfmiddlewaretoken")

    def test_login_normaliza_username_e_registra_ultimo_login(self):
        resposta = self.client.post(self.url_login, {"username": " ADMIN ", "password": SENHA})
        self.assertRedirects(resposta, reverse("admin:index"), fetch_redirect_response=False)
        self.usuario.refresh_from_db()
        self.assertIsNotNone(self.usuario.last_login)

    def test_login_invalido_tem_mensagem_neutra(self):
        for username, senha in [("admin", "errada"), ("naoexiste", SENHA)]:
            resposta = self.client.post(self.url_login, {"username": username, "password": senha})
            self.assertEqual(resposta.status_code, 200)
            self.assertContains(resposta, "Nome de usuário ou senha inválidos.")
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_excluido_nao_entra(self):
        self.usuario.excluir()
        resposta = self.client.post(self.url_login, {"username": "admin", "password": SENHA})
        self.assertContains(resposta, "Nome de usuário ou senha inválidos.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_sessao_de_usuario_desativado_deixa_de_valer(self):
        self.client.force_login(self.usuario)
        get_user_model().objects.filter(pk=self.usuario.pk).update(is_active=False)
        resposta = self.client.get(reverse("admin:index"))
        self.assertEqual(resposta.status_code, 302)

    def test_logout_somente_por_post(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("users:logout")).status_code, 405)
        resposta = self.client.post(reverse("users:logout"))
        self.assertRedirects(resposta, self.url_login, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_redirect_externo_no_next_e_ignorado(self):
        resposta = self.client.post(
            self.url_login, {"username": "admin", "password": SENHA, "next": "https://malicioso.example/"}
        )
        self.assertRedirects(resposta, reverse("admin:index"), fetch_redirect_response=False)


@override_settings(STORAGES=STORAGES_DE_TESTE)
class AdminUsuarioTests(TestCase):
    def setUp(self):
        self.gestor = criar_usuario(username="gestor")
        self.gestor.user_permissions.add(
            Permission.objects.get(codename="view_usuario"),
            Permission.objects.get(codename="change_usuario"),
        )
        self.alvo = criar_usuario(username="alvo")
        self.raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)

    def url_alteracao(self, usuario):
        return reverse("admin:users_usuario_change", args=[usuario.pk])

    def test_usuario_sem_permissao_nao_ve_usuarios(self):
        self.client.force_login(self.alvo)
        self.assertEqual(self.client.get(reverse("admin:users_usuario_changelist")).status_code, 403)

    def test_gestor_nao_concede_privilegios(self):
        self.client.force_login(self.gestor)
        resposta = self.client.post(
            self.url_alteracao(self.gestor),
            {"username": "gestor", "email": "gestor@exemplo.com", "nome": "Gestor", "is_active": "on",
             "is_superuser": "on", "user_permissions": [Permission.objects.get(codename="delete_usuario").pk]},
        )
        self.assertEqual(resposta.status_code, 302)
        self.gestor.refresh_from_db()
        self.assertFalse(self.gestor.is_superuser)
        self.assertFalse(self.gestor.has_perm("users.delete_usuario"))

    def test_gestor_nao_altera_superusuario(self):
        self.client.force_login(self.gestor)
        resposta = self.client.post(
            self.url_alteracao(self.raiz),
            {"username": "raiz", "email": "invasor@exemplo.com", "nome": "Raiz", "is_active": "on"},
        )
        self.assertEqual(resposta.status_code, 403)
        self.raiz.refresh_from_db()
        self.assertEqual(self.raiz.email, "raiz@exemplo.com")

    def test_sem_exclusao_fisica(self):
        self.client.force_login(self.raiz)
        url = reverse("admin:users_usuario_delete", args=[self.alvo.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=self.alvo.pk).exists())

    def test_acao_de_exclusao_logica(self):
        self.client.force_login(self.gestor)
        self.client.post(
            reverse("admin:users_usuario_changelist"),
            {"action": "excluir_logicamente", "_selected_action": [self.alvo.pk, self.raiz.pk, self.gestor.pk]},
        )
        for usuario in (self.alvo, self.raiz, self.gestor):
            usuario.refresh_from_db()
        self.assertIsNotNone(self.alvo.deleted_at)
        self.assertIsNone(self.raiz.deleted_at)  # superusuário: só outro superusuário exclui
        self.assertIsNone(self.gestor.deleted_at)  # a própria conta nunca


class UsuarioMeAPITests(APITestCase):
    url = "/api/usuarios/me/"

    def test_anonimo_recebe_401(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_retorna_somente_o_proprio_usuario_sem_hash(self):
        usuario = criar_usuario()
        criar_usuario(username="outro")
        self.client.force_authenticate(usuario)
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertEqual(dados["username"], "admin")
        self.assertEqual(set(dados), {"id", "nome", "username", "email", "ativo", "ultimo_login_em", "created_at"})

    def test_somente_leitura(self):
        self.client.force_authenticate(criar_usuario())
        self.assertEqual(self.client.post(self.url, {"username": "x"}).status_code, 405)
        self.assertEqual(self.client.get("/api/usuarios/").status_code, 404)
