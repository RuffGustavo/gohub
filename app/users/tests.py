import datetime
import logging
import re
from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.models import Group, Permission
from django.core import mail
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


def permissao(codename):
    return Permission.objects.get(content_type__app_label="users", codename=codename)


def dar_acesso_admin(usuario):
    usuario.user_permissions.add(permissao("acessar_admin"))


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
        self.assertRedirects(resposta, reverse("users:painel"), fetch_redirect_response=False)
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
        self.assertRedirects(resposta, reverse("users:painel"), fetch_redirect_response=False)

    def test_login_tem_link_de_redefinicao_e_nao_tem_cadastro(self):
        resposta = self.client.get(self.url_login)
        self.assertContains(resposta, reverse("users:redefinir-senha"))
        self.assertNotContains(resposta, "register")

    def test_usuario_logado_vai_direto_para_o_painel(self):
        self.client.force_login(self.usuario)
        resposta = self.client.get(self.url_login)
        self.assertRedirects(resposta, reverse("users:painel"), fetch_redirect_response=False)


@override_settings(STORAGES=STORAGES_DE_TESTE)
class AdminUsuarioTests(TestCase):
    def setUp(self):
        self.gestor = criar_usuario(username="gestor")
        self.gestor.user_permissions.add(
            permissao("acessar_admin"), permissao("view_usuario"), permissao("change_usuario")
        )
        self.alvo = criar_usuario(username="alvo")
        self.raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)

    def url_alteracao(self, usuario):
        return reverse("admin:users_usuario_change", args=[usuario.pk])

    def test_usuario_sem_permissao_nao_ve_usuarios(self):
        dar_acesso_admin(self.alvo)  # entra no admin, mas sem view_usuario
        self.client.force_login(self.alvo)
        self.assertEqual(self.client.get(reverse("admin:users_usuario_changelist")).status_code, 403)

    def test_gestor_nao_concede_privilegios(self):
        self.client.force_login(self.gestor)
        resposta = self.client.post(
            self.url_alteracao(self.alvo),
            {"username": "alvo", "email": "alvo@exemplo.com", "nome": "Alvo", "is_active": "on",
             "is_superuser": "on", "user_permissions": [Permission.objects.get(codename="delete_usuario").pk]},
        )
        self.assertEqual(resposta.status_code, 302)
        alvo = get_user_model().objects.get(pk=self.alvo.pk)
        self.assertFalse(alvo.is_superuser)
        self.assertFalse(alvo.user_permissions.exists())
        self.assertFalse(alvo.has_perm("users.delete_usuario"))

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


@override_settings(STORAGES=STORAGES_DE_TESTE)
class AcessoAdminTests(TestCase):
    """O Django Admin fica restrito a superusuários e a quem tem "users.acessar_admin"."""

    def setUp(self):
        self.comum = criar_usuario(username="comum")

    def test_usuario_comum_nao_e_staff(self):
        self.assertFalse(self.comum.is_staff)

    def test_usuario_comum_e_redirecionado_para_o_login_do_admin(self):
        self.client.force_login(self.comum)
        resposta = self.client.get(reverse("admin:index"))
        self.assertRedirects(
            resposta, f"{reverse('admin:login')}?next={reverse('admin:index')}", fetch_redirect_response=False
        )

    def test_usuario_comum_nao_entra_pelo_login_do_admin(self):
        resposta = self.client.post(
            reverse("admin:login"), {"username": "comum", "password": SENHA, "next": reverse("admin:index")}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_permissao_direta_libera_o_admin(self):
        dar_acesso_admin(self.comum)
        self.client.force_login(self.comum)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_permissao_por_grupo_libera_o_admin(self):
        grupo = Group.objects.create(name="Administradores")
        grupo.permissions.add(permissao("acessar_admin"))
        self.comum.groups.add(grupo)
        self.client.force_login(self.comum)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_superusuario_entra_no_admin(self):
        raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.client.force_login(raiz)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_permissao_nao_vale_para_inativo_ou_excluido(self):
        dar_acesso_admin(self.comum)
        self.comum.excluir()
        self.comum.refresh_from_db()
        self.assertFalse(self.comum.is_staff)

    def test_gestor_nao_concede_acesso_ao_admin(self):
        gestor = criar_usuario(username="gestor")
        gestor.user_permissions.add(
            permissao("acessar_admin"), permissao("view_usuario"), permissao("change_usuario")
        )
        self.client.force_login(gestor)
        self.client.post(
            reverse("admin:users_usuario_change", args=[self.comum.pk]),
            {"username": "comum", "email": "comum@exemplo.com", "nome": "Comum", "is_active": "on",
             "user_permissions": [permissao("acessar_admin").pk]},
        )
        self.comum.refresh_from_db()
        self.assertFalse(get_user_model().objects.get(pk=self.comum.pk).is_staff)


@override_settings(STORAGES=STORAGES_DE_TESTE)
class EscaladaPorGruposTests(TestCase):
    """R1: só superusuário cria, altera ou exclui grupos (e, com isso, concede permissões)."""

    def setUp(self):
        self.grupo = Group.objects.create(name="Equipe")
        self.gestor = criar_usuario(username="gestor")
        self.gestor.groups.add(self.grupo)
        self.gestor.user_permissions.add(
            permissao("acessar_admin"),
            Permission.objects.get(codename="view_group"),
            Permission.objects.get(codename="add_group"),
            Permission.objects.get(codename="change_group"),
            Permission.objects.get(codename="delete_group"),
        )
        self.client.force_login(self.gestor)

    def test_regressao_change_group_nao_eleva_o_proprio_privilegio(self):
        # Reproduz o diagnóstico [1]: antes da correção, isto devolvia 302 e o gestor ganhava change_usuario.
        alvo = [permissao("delete_usuario").pk, permissao("change_usuario").pk,
                Permission.objects.get(codename="change_permission").pk]
        resposta = self.client.post(
            reverse("admin:auth_group_change", args=[self.grupo.pk]), {"name": "Equipe", "permissions": alvo}
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(self.grupo.permissions.exists())
        self.assertFalse(get_user_model().objects.get(pk=self.gestor.pk).has_perm("users.change_usuario"))

    def test_nao_superusuario_nao_cria_nem_exclui_grupo(self):
        resposta = self.client.post(
            reverse("admin:auth_group_add"), {"name": "Nova", "permissions": [permissao("change_usuario").pk]}
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Group.objects.filter(name="Nova").exists())
        resposta = self.client.post(reverse("admin:auth_group_delete", args=[self.grupo.pk]), {"post": "yes"})
        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(Group.objects.filter(pk=self.grupo.pk).exists())

    def test_nao_superusuario_ainda_consulta_grupos(self):
        self.assertEqual(self.client.get(reverse("admin:auth_group_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:auth_group_change", args=[self.grupo.pk])).status_code, 200)

    def test_permission_nao_e_editavel_no_admin(self):
        self.gestor.user_permissions.add(Permission.objects.get(codename="change_permission"))
        self.assertEqual(self.client.get("/admin/auth/permission/").status_code, 404)

    def test_superusuario_continua_gerenciando_grupos(self):
        raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.client.force_login(raiz)
        resposta = self.client.post(
            reverse("admin:auth_group_change", args=[self.grupo.pk]),
            {"name": "Equipe", "permissions": [permissao("view_usuario").pk]},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(self.grupo.permissions.filter(codename="view_usuario").exists())


@override_settings(STORAGES=STORAGES_DE_TESTE)
class ContasComPrivilegioTests(TestCase):
    """R2: só superusuário gerencia contas com qualquer privilégio (grupo, permissão direta,
    acesso ao admin ou superusuário); um não superusuário nunca define esses campos em ninguém."""

    NOVA = "Senha-do-invasor-123"

    def setUp(self):
        self.gestor = criar_usuario(username="gestor")
        self.gestor.user_permissions.add(
            permissao("acessar_admin"), permissao("view_usuario"),
            permissao("add_usuario"), permissao("change_usuario"),
        )
        self.chefe = criar_usuario(username="chefe", email="chefe@exemplo.com")
        self.chefe.user_permissions.add(permissao("acessar_admin"), permissao("delete_usuario"))
        self.comum = criar_usuario(username="comum", email="comum@exemplo.com")
        self.client.force_login(self.gestor)

    def url(self, usuario, acao="change"):
        if acao == "password_change":
            # Nome fixo da rota no UserAdmin do Django, mesmo com usuário customizado.
            return reverse("admin:auth_user_password_change", args=[usuario.pk])
        return reverse(f"admin:users_usuario_{acao}", args=[usuario.pk])

    def dados(self, usuario, **extra):
        return {"username": usuario.username, "email": usuario.email, "nome": usuario.nome, "is_active": "on", **extra}

    def test_regressao_gestor_nao_troca_senha_de_conta_com_privilegio(self):
        # Diagnóstico [3]: antes, 302 e a senha do chefe era trocada.
        resposta = self.client.post(self.url(self.chefe, "password_change"),
                                    {"password1": self.NOVA, "password2": self.NOVA})
        self.assertEqual(resposta.status_code, 403)
        self.chefe.refresh_from_db()
        self.assertTrue(self.chefe.check_password(SENHA))

    def test_regressao_gestor_nao_troca_email_de_conta_com_privilegio(self):
        # Diagnóstico [4]: antes, 302 e o e-mail virava o do invasor.
        resposta = self.client.post(self.url(self.chefe), self.dados(self.chefe, email="invasor@exemplo.com"))
        self.assertEqual(resposta.status_code, 403)
        self.chefe.refresh_from_db()
        self.assertEqual(self.chefe.email, "chefe@exemplo.com")

    def test_privilegio_por_grupo_tambem_protege(self):
        grupo = Group.objects.create(name="Leitores")
        self.comum.groups.add(grupo)
        resposta = self.client.post(self.url(self.comum), self.dados(self.comum, email="x@exemplo.com"))
        self.assertEqual(resposta.status_code, 403)

    def test_gestor_nao_desativa_conta_com_privilegio(self):
        dados = self.dados(self.chefe)
        dados.pop("is_active")
        self.assertEqual(self.client.post(self.url(self.chefe), dados).status_code, 403)
        self.chefe.refresh_from_db()
        self.assertTrue(self.chefe.is_active)

    def test_gestor_nao_altera_a_propria_conta(self):
        # A conta do gestor tem privilégios: só um superusuário a altera (inclusive e-mail e senha).
        resposta = self.client.post(self.url(self.gestor), self.dados(self.gestor, email="outro@exemplo.com"))
        self.assertEqual(resposta.status_code, 403)
        resposta = self.client.post(self.url(self.gestor, "password_change"),
                                    {"password1": self.NOVA, "password2": self.NOVA})
        self.assertEqual(resposta.status_code, 403)

    def test_gestor_ve_conta_com_privilegio_somente_leitura(self):
        self.assertEqual(self.client.get(self.url(self.chefe)).status_code, 200)

    def test_exclusao_logica_ignora_conta_com_privilegio(self):
        self.client.post(
            reverse("admin:users_usuario_changelist"),
            {"action": "excluir_logicamente", "_selected_action": [self.chefe.pk, self.comum.pk]},
        )
        self.chefe.refresh_from_db()
        self.comum.refresh_from_db()
        self.assertIsNone(self.chefe.deleted_at)
        self.assertIsNotNone(self.comum.deleted_at)

    def test_gestor_altera_usuario_sem_privilegio(self):
        resposta = self.client.post(self.url(self.comum), self.dados(self.comum, nome="Comum Silva"))
        self.assertEqual(resposta.status_code, 302)
        self.comum.refresh_from_db()
        self.assertEqual(self.comum.nome, "Comum Silva")

    def test_criacao_por_nao_superusuario_nao_define_privilegios(self):
        grupo = Group.objects.create(name="Admins")
        grupo.permissions.add(permissao("acessar_admin"))
        resposta = self.client.post(reverse("admin:users_usuario_add"), {
            "username": "novo", "email": "novo@exemplo.com", "nome": "Novo",
            "password1": SENHA, "password2": SENHA,
            "is_superuser": "on", "is_staff": "on", "groups": [grupo.pk],
            "user_permissions": [permissao("acessar_admin").pk, permissao("change_usuario").pk],
        })
        self.assertEqual(resposta.status_code, 302)
        novo = get_user_model().objects.get(username="novo")
        self.assertFalse(novo.is_superuser)
        self.assertFalse(novo.groups.exists())
        self.assertFalse(novo.user_permissions.exists())
        self.assertFalse(novo.is_staff)

    def test_superusuario_gerencia_conta_com_privilegio(self):
        raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.client.force_login(raiz)
        resposta = self.client.post(self.url(self.chefe), self.dados(self.chefe, nome="Chefe Novo"))
        self.assertEqual(resposta.status_code, 302)
        self.chefe.refresh_from_db()
        self.assertEqual(self.chefe.nome, "Chefe Novo")


@override_settings(STORAGES=STORAGES_DE_TESTE)
class SessoesEAvisoDeEmailTests(TestCase):
    """Troca de senha ou de e-mail encerra todas as sessões da pessoa; troca de e-mail avisa o antigo."""

    def setUp(self):
        self.raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.pessoa = criar_usuario(username="pessoa", email="pessoa@exemplo.com")
        self.sessao_da_pessoa = self.client_class()
        self.sessao_da_pessoa.force_login(self.pessoa)
        self.client.force_login(self.raiz)

    def pessoa_logada(self):
        return self.sessao_da_pessoa.get(reverse("users:painel")).status_code == 200

    def test_troca_de_email_encerra_sessoes_e_avisa_o_email_antigo(self):
        self.assertTrue(self.pessoa_logada())
        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.post(
                reverse("admin:users_usuario_change", args=[self.pessoa.pk]),
                {"username": "pessoa", "email": "Nova@Exemplo.com", "nome": "Pessoa", "is_active": "on"},
            )
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(self.pessoa_logada())
        avisos = [m for m in mail.outbox if m.to == ["pessoa@exemplo.com"]]
        self.assertEqual(len(avisos), 1)
        self.assertIn("e-mail", avisos[0].subject.lower())
        self.assertNotIn("nova@exemplo.com", avisos[0].body)  # o novo e-mail aparece mascarado
        self.assertIn("n***@exemplo.com", avisos[0].body)

    def test_troca_de_senha_pelo_admin_encerra_sessoes(self):
        self.assertTrue(self.pessoa_logada())
        resposta = self.client.post(
            reverse("admin:auth_user_password_change", args=[self.pessoa.pk]),
            {"password1": "Outra-s3nha-bem-forte", "password2": "Outra-s3nha-bem-forte"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(self.pessoa_logada())

    def test_superusuario_que_troca_o_proprio_email_continua_logado(self):
        resposta = self.client.post(
            reverse("admin:users_usuario_change", args=[self.raiz.pk]),
            {"username": "raiz", "email": "raiz2@exemplo.com", "nome": "Raiz", "is_active": "on",
             "is_superuser": "on"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_salvar_sem_mudar_email_nao_derruba_sessao(self):
        self.client.post(
            reverse("admin:users_usuario_change", args=[self.pessoa.pk]),
            {"username": "pessoa", "email": "pessoa@exemplo.com", "nome": "Pessoa Nova", "is_active": "on"},
        )
        self.assertTrue(self.pessoa_logada())
        self.assertEqual(len(mail.outbox), 0)


@override_settings(STORAGES=STORAGES_DE_TESTE)
class RotasDoClienteTests(TestCase):
    """A raiz (/) é o login do cliente; /login/ é atalho; o admin continua separado."""

    def setUp(self):
        self.usuario = criar_usuario()

    def test_raiz_mostra_o_login(self):
        resposta = self.client.get("/")
        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, "users/login.html")
        self.assertContains(resposta, 'action="/"')

    def test_atalho_login_redireciona_para_a_raiz_preservando_next(self):
        resposta = self.client.get("/login/?next=/perfil/")
        self.assertRedirects(resposta, "/?next=/perfil/", fetch_redirect_response=False)

    def test_login_pela_raiz_vai_para_o_painel(self):
        resposta = self.client.post("/", {"username": "admin", "password": SENHA})
        self.assertRedirects(resposta, reverse("users:painel"), fetch_redirect_response=False)

    def test_login_respeita_next_interno(self):
        resposta = self.client.post("/", {"username": "admin", "password": SENHA, "next": "/perfil/"})
        self.assertRedirects(resposta, "/perfil/", fetch_redirect_response=False)

    def test_paginas_do_cliente_exigem_login(self):
        for nome in ["users:painel", "users:perfil", "users:perfil-editar", "users:perfil-seguranca"]:
            url = reverse(nome)
            resposta = self.client.get(url)
            self.assertRedirects(resposta, f"/?next={url}", fetch_redirect_response=False)

    def test_cliente_nao_usa_o_admin(self):
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("admin:index"))
        self.assertRedirects(
            resposta, f"{reverse('admin:login')}?next={reverse('admin:index')}", fetch_redirect_response=False
        )


@override_settings(STORAGES=STORAGES_DE_TESTE)
class PainelTests(TestCase):
    def setUp(self):
        self.url = reverse("users:painel")
        self.usuario = criar_usuario()

    def test_painel_com_dados_de_exemplo_identificados(self):
        self.client.force_login(self.usuario)
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, "users/painel.html")
        self.assertContains(resposta, "Dados de exemplo")
        self.assertContains(resposta, 'id="dados-exemplo-painel"')
        self.assertContains(resposta, "assets/js/gohub/paginas/painel.js")
        self.assertContains(resposta, "Ana Souza")  # card "Sua conta" com dados reais

    def test_usuario_comum_nao_ve_atalho_do_admin(self):
        self.client.force_login(self.usuario)
        self.assertNotContains(self.client.get(self.url), reverse("admin:index"))

    def test_usuario_com_acesso_ve_atalho_do_admin(self):
        dar_acesso_admin(self.usuario)
        self.client.force_login(self.usuario)
        self.assertContains(self.client.get(self.url), reverse("admin:index"))


@override_settings(STORAGES=STORAGES_DE_TESTE)
class PerfilTests(TestCase):
    NOVA_SENHA = "Outra-s3nha-bem-forte"

    def setUp(self):
        self.usuario = criar_usuario(email="ana@exemplo.com")
        self.outro = criar_usuario(username="outro", email="outro@exemplo.com", nome="Outro Usuario")
        self.client.force_login(self.usuario)
        self.url_editar = reverse("users:perfil-editar")

    def test_perfil_mostra_somente_a_propria_conta(self):
        resposta = self.client.get(reverse("users:perfil"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "ana@exemplo.com")
        self.assertNotContains(resposta, "outro@exemplo.com")

    def test_edita_o_nome_sem_pedir_senha(self):
        resposta = self.client.post(self.url_editar, {"nome": "  Ana   Maria ", "email": "ana@exemplo.com"})
        self.assertRedirects(resposta, reverse("users:perfil"), fetch_redirect_response=False)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.nome, "Ana Maria")

    def test_nao_altera_username_status_nem_privilegios(self):
        self.client.post(self.url_editar, {
            "nome": "Ana", "email": "ana@exemplo.com", "username": "hacker",
            "is_superuser": "on", "is_active": "", "user_permissions": [permissao("acessar_admin").pk],
        })
        usuario = get_user_model().objects.get(pk=self.usuario.pk)
        self.assertEqual(usuario.username, "admin")
        self.assertFalse(usuario.is_superuser)
        self.assertTrue(usuario.is_active)
        self.assertFalse(usuario.user_permissions.exists())

    def test_troca_de_email_exige_a_senha_atual(self):
        for senha in ["", "senha-errada"]:
            resposta = self.client.post(self.url_editar, {"nome": "Ana", "email": "nova@exemplo.com", "senha_atual": senha})
            self.assertEqual(resposta.status_code, 200)
            self.assertContains(resposta, "Informe sua senha atual para trocar o e-mail.")
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.email, "ana@exemplo.com")

    def test_form_invalido_nao_altera_o_usuario_exibido_na_pagina(self):
        resposta = self.client.post(self.url_editar, {"nome": "Nome Nao Salvo", "email": "nova@exemplo.com"})
        self.assertEqual(resposta.context["user"].nome, "Ana Souza")

    def test_troca_de_email_com_senha_mantem_esta_sessao_encerra_as_outras_e_avisa(self):
        outra_sessao = self.client_class()
        outra_sessao.force_login(self.usuario)
        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.post(
                self.url_editar, {"nome": "Ana", "email": "Nova@Exemplo.com", "senha_atual": SENHA}
            )
        self.assertRedirects(resposta, reverse("users:perfil"), fetch_redirect_response=False)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.email, "nova@exemplo.com")
        self.assertEqual(self.client.get(reverse("users:perfil")).status_code, 200)
        self.assertEqual(outra_sessao.get(reverse("users:perfil")).status_code, 302)
        self.assertEqual([m.to for m in mail.outbox], [["ana@exemplo.com"]])

    def test_email_de_outra_conta_e_recusado(self):
        resposta = self.client.post(self.url_editar, {"nome": "Ana", "email": "outro@exemplo.com", "senha_atual": SENHA})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Já existe um usuário com este e-mail.")

    def test_troca_de_senha_exige_a_senha_atual(self):
        resposta = self.client.post(reverse("users:perfil-seguranca"), {
            "old_password": "errada", "new_password1": self.NOVA_SENHA, "new_password2": self.NOVA_SENHA,
        })
        self.assertContains(resposta, "A senha atual está incorreta.")
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(SENHA))

    def test_troca_de_senha_mantem_esta_sessao_e_encerra_as_outras(self):
        outra_sessao = self.client_class()
        outra_sessao.force_login(self.usuario)
        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.post(reverse("users:perfil-seguranca"), {
                "old_password": SENHA, "new_password1": self.NOVA_SENHA, "new_password2": self.NOVA_SENHA,
            })
        self.assertRedirects(resposta, reverse("users:perfil-seguranca"), fetch_redirect_response=False)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(self.NOVA_SENHA))
        self.assertEqual(self.client.get(reverse("users:painel")).status_code, 200)
        self.assertEqual(outra_sessao.get(reverse("users:painel")).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)  # aviso de senha alterada


@override_settings(
    STORAGES=STORAGES_DE_TESTE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="GoHub <nao-responda@exemplo.com>",
)
class RedefinicaoDeSenhaTests(TestCase):
    NOVA_SENHA = "Outra-s3nha-bem-forte"

    def setUp(self):
        self.usuario = criar_usuario(email="ana@exemplo.com")
        self.url = reverse("users:redefinir-senha")

    def pedir(self, email):
        return self.client.post(self.url, {"email": email})

    def link_do_email(self):
        self.assertEqual(len(mail.outbox), 1)
        return re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[0].body).group(1)

    def test_pagina_de_pedido(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, "users/redefinir_senha.html")
        self.assertContains(resposta, "csrfmiddlewaretoken")

    def test_envia_link_para_conta_ativa(self):
        resposta = self.pedir("  ANA@Exemplo.com ")
        self.assertRedirects(resposta, reverse("users:redefinir-senha-enviado"))
        self.assertEqual(mail.outbox[0].to, ["ana@exemplo.com"])
        self.assertEqual(mail.outbox[0].subject, "Redefinição de senha do GoHub")
        self.assertIn("/redefinir-senha/", self.link_do_email())

    def test_resposta_igual_para_email_sem_conta_inativa_ou_excluida(self):
        criar_usuario(username="inativo", email="inativo@exemplo.com", is_active=False)
        criar_usuario(username="excluido", email="excluido@exemplo.com").excluir()
        for email in ["ninguem@exemplo.com", "inativo@exemplo.com", "excluido@exemplo.com"]:
            resposta = self.pedir(email)
            self.assertRedirects(resposta, reverse("users:redefinir-senha-enviado"))
        self.assertEqual(len(mail.outbox), 0)

    def test_pagina_de_enviado_usa_template_de_sucesso(self):
        resposta = self.client.get(reverse("users:redefinir-senha-enviado"))
        self.assertTemplateUsed(resposta, "users/sucesso.html")
        self.assertContains(resposta, "Verifique seu e-mail")

    def test_email_invalido(self):
        resposta = self.pedir("nao-e-email")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'aria-invalid="true"')
        self.assertEqual(len(mail.outbox), 0)

    def test_fluxo_completo_e_link_de_uso_unico(self):
        self.pedir("ana@exemplo.com")
        link = self.link_do_email()
        # O Django troca o token da URL por "set-password" (guardado na sessão) antes do formulário.
        resposta = self.client.get(link, follow=True)
        self.assertTemplateUsed(resposta, "users/redefinir_senha_confirmar.html")
        self.assertTrue(resposta.context["validlink"])
        url_form = resposta.redirect_chain[-1][0]
        self.assertNotIn(link.rstrip("/").split("/")[-1], url_form)

        resposta = self.client.post(url_form, {"new_password1": self.NOVA_SENHA, "new_password2": self.NOVA_SENHA})
        self.assertRedirects(resposta, reverse("users:redefinir-senha-concluido"))
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(self.NOVA_SENHA))
        self.assertTrue(self.usuario.password.startswith("argon2$argon2id$"))
        self.assertNotIn("_auth_user_id", self.client.session)  # não faz login automático

        concluido = self.client.get(reverse("users:redefinir-senha-concluido"))
        self.assertTemplateUsed(concluido, "users/sucesso.html")
        self.assertContains(concluido, "Senha redefinida")

        reuso = self.client.get(link, follow=True)
        self.assertFalse(reuso.context["validlink"])
        self.assertContains(reuso, "Link inválido")

    def test_nova_senha_passa_pelos_validadores(self):
        self.pedir("ana@exemplo.com")
        url_form = self.client.get(self.link_do_email(), follow=True).redirect_chain[-1][0]
        resposta = self.client.post(url_form, {"new_password1": "12345678", "new_password2": "12345678"})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'aria-invalid="true"')
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(SENHA))

    def test_link_adulterado_e_invalido(self):
        url = reverse("users:redefinir-senha-confirmar", kwargs={"uidb64": "abc", "token": "x-y"})
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["validlink"])

    def test_redefinicao_encerra_sessoes_abertas(self):
        outro = self.client_class()
        outro.force_login(self.usuario)
        self.pedir("ana@exemplo.com")
        url_form = self.client.get(self.link_do_email(), follow=True).redirect_chain[-1][0]
        self.client.post(url_form, {"new_password1": self.NOVA_SENHA, "new_password2": self.NOVA_SENHA})
        resposta = outro.get(reverse("users:painel"))
        self.assertEqual(resposta.status_code, 302)


@override_settings(
    STORAGES=STORAGES_DE_TESTE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="GoHub <nao-responda@exemplo.com>",
)
class AuditoriaEAvisosTests(TestCase):
    """Eventos de segurança registrados em "gohub.auditoria", sem senha, token nem e-mail completo."""

    NOVA_SENHA = "Outra-s3nha-bem-forte"

    def setUp(self):
        self.usuario = criar_usuario(email="ana.souza@exemplo.com")

    def eventos(self, logs):
        return [r.getMessage() for r in logs.records if r.name == "gohub.auditoria"]

    def test_login_com_sucesso_e_falha(self):
        with self.assertLogs("gohub.auditoria", level="INFO") as logs:
            self.client.post(reverse("users:login"), {"username": "admin", "password": "errada-123"})
            self.client.post(reverse("users:login"), {"username": "naoexiste", "password": "x"})
            self.client.post(reverse("users:login"), {"username": "admin", "password": SENHA})
            self.client.post(reverse("users:logout"))
        eventos = self.eventos(logs)
        self.assertIn(f"evento=login_falha ip=127.0.0.1 usuario_id={self.usuario.pk} caminho=/", eventos)
        self.assertIn("evento=login_falha ip=127.0.0.1 conta=inexistente caminho=/", eventos)
        self.assertIn(f"evento=login ip=127.0.0.1 usuario_id={self.usuario.pk} caminho=/", eventos)
        self.assertIn(f"evento=logout ip=127.0.0.1 usuario_id={self.usuario.pk}", eventos)
        self.assertNotIn("naoexiste", " ".join(eventos))  # o texto digitado não vai para o log

    def test_falha_na_api_basic_tambem_e_registrada(self):
        import base64

        credencial = base64.b64encode(b"admin:errada").decode()
        with self.assertLogs("gohub.auditoria", level="INFO") as logs:
            self.client.get("/api/usuarios/me/", HTTP_AUTHORIZATION=f"Basic {credencial}")
        self.assertTrue(any(e.startswith("evento=login_falha") and "caminho=/api/usuarios/me/" in e
                            for e in self.eventos(logs)))

    def test_redefinicao_registra_pedido_envio_e_conclusao_e_avisa_o_titular(self):
        with self.assertLogs("gohub.auditoria", level="INFO") as logs:
            self.client.post(reverse("users:redefinir-senha"), {"email": "ana.souza@exemplo.com"})
            link = re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[0].body).group(1)
            url_form = self.client.get(link, follow=True).redirect_chain[-1][0]
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url_form, {"new_password1": self.NOVA_SENHA, "new_password2": self.NOVA_SENHA})
        eventos = self.eventos(logs)
        self.assertIn("evento=redefinicao_solicitada ip=127.0.0.1 destino=a***@exemplo.com", eventos)
        self.assertIn(f"evento=redefinicao_link_enviado ip=127.0.0.1 usuario_id={self.usuario.pk}", eventos)
        self.assertIn(f"evento=redefinicao_concluida ip=127.0.0.1 usuario_id={self.usuario.pk}", eventos)
        self.assertIn(f"evento=senha_alterada ip=- usuario_id={self.usuario.pk}", eventos)
        aviso = mail.outbox[-1]
        self.assertEqual(aviso.to, ["ana.souza@exemplo.com"])
        self.assertEqual(aviso.subject, "A senha da sua conta do GoHub foi alterada")

    def test_troca_de_senha_pelo_admin_avisa_o_titular(self):
        raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        self.client.force_login(raiz)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("admin:auth_user_password_change", args=[self.usuario.pk]),
                             {"password1": self.NOVA_SENHA, "password2": self.NOVA_SENHA})
        self.assertEqual([m.to for m in mail.outbox], [["ana.souza@exemplo.com"]])

    def test_criacao_de_conta_e_rehash_nao_geram_aviso(self):
        with self.captureOnCommitCallbacks(execute=True):
            novo = criar_usuario(username="novo")
            # check_password regrava o hash quando os parâmetros do hasher mudam (rehash): não é troca.
            get_user_model().objects.filter(pk=novo.pk).update(
                password=__import__("django.contrib.auth.hashers", fromlist=["make_password"]).make_password(
                    SENHA, hasher="pbkdf2_sha256"))
            novo.refresh_from_db()
            self.assertTrue(novo.check_password(SENHA))
        novo.refresh_from_db()
        self.assertTrue(novo.password.startswith("argon2$"))  # houve rehash
        self.assertEqual(len(mail.outbox), 0)

    def test_pedido_para_email_sem_conta_registra_so_o_pedido(self):
        with self.assertLogs("gohub.auditoria", level="INFO") as logs:
            self.client.post(reverse("users:redefinir-senha"), {"email": "ninguem@exemplo.com"})
        self.assertEqual(self.eventos(logs), ["evento=redefinicao_solicitada ip=127.0.0.1 destino=n***@exemplo.com"])


@override_settings(
    STORAGES=STORAGES_DE_TESTE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="GoHub <nao-responda@exemplo.com>",
    PASSWORD_RESET_TIMEOUT=3600,
)
class TokenELogsTests(TestCase):
    NOVA_SENHA = "Outra-s3nha-bem-forte"

    def setUp(self):
        self.usuario = criar_usuario(email="ana.souza@exemplo.com")

    def pedir_link(self):
        self.client.post(reverse("users:redefinir-senha"), {"email": "ana.souza@exemplo.com"})
        return re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[-1].body).group(1)

    def abrir(self, link, adiantar_segundos=0):
        agora = datetime.datetime.now() + datetime.timedelta(seconds=adiantar_segundos)
        with mock.patch.object(PasswordResetTokenGenerator, "_now", return_value=agora):
            return self.client.get(link, follow=True)

    def test_token_vale_dentro_do_prazo(self):
        self.assertTrue(self.abrir(self.pedir_link(), adiantar_segundos=3500).context["validlink"])

    def test_token_expira_apos_password_reset_timeout(self):
        resposta = self.abrir(self.pedir_link(), adiantar_segundos=3601)
        self.assertFalse(resposta.context["validlink"])
        self.assertContains(resposta, "Link inválido")

    def test_link_antigo_deixa_de_valer_depois_da_troca_de_email(self):
        link = self.pedir_link()
        self.usuario.email = "outro@exemplo.com"
        self.usuario.save()
        self.assertFalse(self.client.get(link, follow=True).context["validlink"])

    def test_nenhum_log_contem_senha_token_ou_email_completo(self):
        raiz = get_user_model().objects.create_superuser("raiz", "raiz@exemplo.com", "Raiz", SENHA)
        with self.assertLogs(level=logging.DEBUG) as logs, self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("users:login"), {"username": "admin", "password": "Senha-errada-999"})
            self.client.post(reverse("users:login"), {"username": "Senha-no-campo-errado", "password": "x"})
            self.client.post(reverse("users:redefinir-senha"), {"email": "ana.souza@exemplo.com"})
            self.client.post(reverse("users:redefinir-senha"), {"email": "ninguem@exemplo.com"})
            link = re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[-1].body).group(1)
            uid, token = link.strip("/").split("/")[-2:]
            url_form = self.client.get(link, follow=True).redirect_chain[-1][0]
            self.client.post(url_form, {"new_password1": self.NOVA_SENHA, "new_password2": self.NOVA_SENHA})
            self.client.force_login(raiz)
            self.client.post(
                reverse("admin:users_usuario_change", args=[self.usuario.pk]),
                {"username": "admin", "email": "novo.email@exemplo.com", "nome": "Ana Souza", "is_active": "on"},
            )
        texto = "\n".join(
            f"{r.getMessage()} {r.exc_text or ''}" for r in logs.records
        )
        self.assertIn("evento=", texto)  # houve registro de auditoria
        for proibido in [SENHA, self.NOVA_SENHA, "Senha-errada-999", "Senha-no-campo-errado", token,
                         "ana.souza@exemplo.com", "novo.email@exemplo.com", "ninguem@exemplo.com"]:
            self.assertNotIn(proibido, texto)


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
