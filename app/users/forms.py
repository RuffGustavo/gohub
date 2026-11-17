from django import forms
from django.contrib.auth.forms import (
    AdminPasswordChangeForm,
    AuthenticationForm,
    BaseUserCreationForm,
    UserChangeForm,
)

from .models import Usuario, normalizar_email, normalizar_nome, normalizar_username


class NormalizarUsuarioMixin:
    """Normaliza antes das validações de formato e unicidade (mesma regra do gatilho)."""

    def clean_username(self):
        return normalizar_username(self.cleaned_data.get("username"))

    def clean_email(self):
        return normalizar_email(self.cleaned_data.get("email"))

    def clean_nome(self):
        return normalizar_nome(self.cleaned_data.get("nome"))


class LoginForm(AuthenticationForm):
    error_messages = {
        # Mensagem única para usuário inexistente, senha errada, inativo ou excluído:
        # não revela quais contas existem.
        "invalid_login": "Nome de usuário ou senha inválidos.",
        "inactive": "Nome de usuário ou senha inválidos.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].label = "Nome de usuário"
        self.fields["password"].label = "Senha"

    def clean_username(self):
        return normalizar_username(self.cleaned_data.get("username"))


class UsuarioCreationForm(NormalizarUsuarioMixin, BaseUserCreationForm):
    """Criação no admin. Sem a opção de senha inutilizável: o CHECK ck_usuario_senha_hash a recusa."""

    class Meta:
        model = Usuario
        fields = ["username", "email", "nome"]


class UsuarioChangeForm(NormalizarUsuarioMixin, UserChangeForm):
    class Meta:
        model = Usuario
        fields = [
            "username",
            "email",
            "nome",
            "password",
            "is_active",
            "is_superuser",
            "groups",
            "user_permissions",
        ]


class UsuarioPasswordChangeForm(AdminPasswordChangeForm):
    """Troca de senha no admin sem a opção "desativar senha" (recusada pelo banco)."""

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields.pop("usable_password", None)
        self.fields["password1"].required = True
        self.fields["password2"].required = True
