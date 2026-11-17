from django import forms
from django.contrib.auth.forms import (
    AdminPasswordChangeForm,
    AuthenticationForm,
    BaseUserCreationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserChangeForm,
)

from .auditoria import registrar
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


class RedefinirSenhaForm(PasswordResetForm):
    """Pede o link de redefinição. A resposta é a mesma exista ou não a conta."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].label = "E-mail"

    def clean_email(self):
        return normalizar_email(self.cleaned_data.get("email"))

    def get_users(self, email):
        # O Django já ignora inativos e senha inutilizável; aqui também os excluídos logicamente.
        return (usuario for usuario in super().get_users(email) if usuario.deleted_at is None)

    def save(self, *args, request=None, **kwargs):
        self._request = request
        return super().save(*args, request=request, **kwargs)

    def send_mail(self, subject_template_name, email_template_name, context, *args, **kwargs):
        # Registra só o id da conta; o contexto (com token) nunca vai para o log.
        registrar("redefinicao_link_enviado", getattr(self, "_request", None), context["user"])
        return super().send_mail(subject_template_name, email_template_name, context, *args, **kwargs)


class NovaSenhaForm(SetPasswordForm):
    """Define a nova senha a partir do link enviado por e-mail."""

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["new_password1"].label = "Nova senha"
        self.fields["new_password2"].label = "Confirme a nova senha"
        self.fields["new_password2"].help_text = "Digite a mesma senha novamente."


class PerfilForm(NormalizarUsuarioMixin, forms.ModelForm):
    """Dados que o próprio usuário edita. Username, status e privilégios não fazem parte."""

    senha_atual = forms.CharField(
        label="Senha atual",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="Obrigatória para trocar o e-mail.",
    )

    class Meta:
        model = Usuario
        fields = ["nome", "email"]

    def clean(self):
        dados = super().clean()
        if "email" in self.changed_data and not self.errors.get("email"):
            # Confirma a identidade: com uma sessão roubada não dá para trocar o e-mail
            # (e, com ele, o destino da redefinição de senha).
            if not self.instance.check_password(dados.get("senha_atual") or ""):
                self.add_error("senha_atual", "Informe sua senha atual para trocar o e-mail.")
        return dados


class AlterarSenhaForm(PasswordChangeForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["old_password"].label = "Senha atual"
        self.fields["new_password1"].label = "Nova senha"
        self.fields["new_password2"].label = "Confirme a nova senha"
        self.fields["new_password2"].help_text = "Digite a mesma senha novamente."
        self.error_messages = {**self.error_messages, "password_incorrect": "A senha atual está incorreta."}


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
