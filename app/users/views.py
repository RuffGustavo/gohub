from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView, UpdateView
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .auditoria import mascarar_email, registrar
from .forms import AlterarSenhaForm, LoginForm, NovaSenhaForm, PerfilForm, RedefinirSenhaForm
from .serializers import UsuarioSerializer


class LoginView(auth_views.LoginView):
    template_name = "users/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    # Django 5: logout só por POST (com CSRF).
    next_page = reverse_lazy("users:login")


class PerfilView(LoginRequiredMixin, TemplateView):
    """Dados da própria conta (sempre request.user: não há id na URL, logo não há IDOR)."""

    template_name = "users/perfil/detalhe.html"


class PerfilEditarView(LoginRequiredMixin, UpdateView):
    template_name = "users/perfil/editar.html"
    form_class = PerfilForm
    success_url = reverse_lazy("users:perfil")

    def get_object(self, queryset=None):
        # Instância nova (não request.user): se o form for inválido, os valores digitados
        # não "vazam" para o cabeçalho e o menu da própria página.
        return get_user_model().objects.get(pk=self.request.user.pk)

    def form_valid(self, form):
        resposta = super().form_valid(form)
        if "email" in form.changed_data:
            # A troca de e-mail encerra as outras sessões (hash da sessão); mantém só esta.
            update_session_auth_hash(self.request, self.object)
        registrar("perfil_atualizado", self.request, self.object, campos=",".join(sorted(form.changed_data)) or "-")
        messages.success(self.request, "Seus dados foram atualizados.")
        return resposta


class AlterarSenhaView(auth_views.PasswordChangeView):
    """Troca de senha pelo próprio usuário (exige a senha atual). O Django mantém esta sessão
    e as demais deixam de valer; o aviso ao titular sai pelo model."""

    template_name = "users/perfil/seguranca.html"
    form_class = AlterarSenhaForm
    success_url = reverse_lazy("users:perfil-seguranca")

    def form_valid(self, form):
        resposta = super().form_valid(form)
        messages.success(self.request, "Sua senha foi alterada. As outras sessões abertas foram encerradas.")
        return resposta


class RedefinirSenhaView(auth_views.PasswordResetView):
    """Etapa 1: pede o e-mail e envia o link de uso único (válido por PASSWORD_RESET_TIMEOUT)."""

    template_name = "users/redefinir_senha.html"
    form_class = RedefinirSenhaForm
    email_template_name = "users/emails/redefinir_senha.txt"
    subject_template_name = "users/emails/redefinir_senha_assunto.txt"
    success_url = reverse_lazy("users:redefinir-senha-enviado")

    def form_valid(self, form):
        registrar("redefinicao_solicitada", self.request, destino=mascarar_email(form.cleaned_data["email"]))
        return super().form_valid(form)


class RedefinirSenhaEnviadoView(auth_views.PasswordResetDoneView):
    """Etapa 2: confirmação neutra (não revela se o e-mail tem conta)."""

    template_name = "users/sucesso.html"
    extra_context = {
        "titulo": "Verifique seu e-mail",
        "mensagem": (
            "Se houver uma conta ativa com o e-mail informado, você receberá em instantes "
            "um link para criar uma nova senha. Confira também a caixa de spam."
        ),
    }


class RedefinirSenhaConfirmarView(auth_views.PasswordResetConfirmView):
    """Etapa 3: valida o link e define a nova senha (o Django tira o token da URL antes do form)."""

    template_name = "users/redefinir_senha_confirmar.html"
    form_class = NovaSenhaForm
    success_url = reverse_lazy("users:redefinir-senha-concluido")

    def form_valid(self, form):
        resposta = super().form_valid(form)
        registrar("redefinicao_concluida", self.request, form.user)
        return resposta


class RedefinirSenhaConcluidoView(auth_views.PasswordResetCompleteView):
    """Etapa 4: senha alterada; as sessões abertas com a senha antiga deixam de valer."""

    template_name = "users/sucesso.html"
    extra_context = {
        "titulo": "Senha redefinida",
        "mensagem": "Sua senha foi alterada. Agora você já pode entrar com a nova senha.",
    }


class UsuarioViewSet(viewsets.GenericViewSet):
    """Dados da conta autenticada."""

    serializer_class = UsuarioSerializer
    # Expõe apenas o próprio usuário, então não exige permissão de model
    # (DjangoModelPermissions dependeria de um queryset de todos os usuários).
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(self.get_serializer(request.user).data)
