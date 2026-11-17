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


# Dados FICTÍCIOS para compor o dashboard enquanto não há módulos de domínio. A página
# identifica cada bloco como exemplo; substitua por consultas reais quando existirem.
DADOS_EXEMPLO = {
    "acessos": {
        "rotulos": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
        "valores": [12, 19, 14, 22, 18, 7, 5],
    },
    "evolucao": {
        "rotulos": ["Jun", "Jul", "Ago", "Set", "Out", "Nov"],
        "series": [
            {"nome": "Concluídas", "valores": [30, 42, 38, 51, 47, 60]},
            {"nome": "Em andamento", "valores": [18, 22, 25, 20, 28, 24]},
        ],
    },
    "distribuicao": {
        "rotulos": ["Concluídas", "Em andamento", "Pendentes"],
        "valores": [60, 24, 16],
    },
}

INDICADORES_EXEMPLO = [
    {"titulo": "Acessos na semana", "valor": "97", "variacao": "12%", "subiu": True, "icone": "ni-signin"},
    {"titulo": "Tarefas concluídas", "valor": "60", "variacao": "28%", "subiu": True, "icone": "ni-check-circle"},
    {"titulo": "Pendências", "valor": "16", "variacao": "9%", "subiu": False, "icone": "ni-clock"},
]

ATIVIDADES_EXEMPLO = [
    {"descricao": "Relatório mensal gerado", "quando": "Hoje, 09:42", "situacao": "Concluída", "cor": "success"},
    {"descricao": "Revisão de cadastro", "quando": "Ontem, 16:10", "situacao": "Em andamento", "cor": "warning"},
    {"descricao": "Importação de planilha", "quando": "12/11, 11:05", "situacao": "Pendente", "cor": "info"},
]


class PainelView(LoginRequiredMixin, TemplateView):
    """Dashboard do cliente, destino após o login (LOGIN_REDIRECT_URL)."""

    template_name = "users/painel.html"
    extra_context = {
        "dados_exemplo": DADOS_EXEMPLO,
        "indicadores_exemplo": INDICADORES_EXEMPLO,
        "atividades_exemplo": ATIVIDADES_EXEMPLO,
        "legenda_distribuicao": list(
            zip(DADOS_EXEMPLO["distribuicao"]["rotulos"], DADOS_EXEMPLO["distribuicao"]["valores"])
        ),
    }


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
