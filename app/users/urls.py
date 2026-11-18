from django.urls import path
from django.views.generic import RedirectView

from app.dashboard.views import PainelPessoalView

from . import views

app_name = "users"

urlpatterns = [
    # A página inicial é o login do cliente; /login/ é mantido como atalho (preserva ?next=).
    path("", views.LoginView.as_view(), name="login"),
    path("login/", RedirectView.as_view(pattern_name="users:login", query_string=True), name="login-atalho"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("painel/", PainelPessoalView.as_view(), name="painel"),
    path("perfil/", views.PerfilView.as_view(), name="perfil"),
    path("perfil/editar/", views.PerfilEditarView.as_view(), name="perfil-editar"),
    path("perfil/seguranca/", views.AlterarSenhaView.as_view(), name="perfil-seguranca"),
    path("redefinir-senha/", views.RedefinirSenhaView.as_view(), name="redefinir-senha"),
    path(
        "redefinir-senha/enviado/",
        views.RedefinirSenhaEnviadoView.as_view(),
        name="redefinir-senha-enviado",
    ),
    path(
        "redefinir-senha/concluido/",
        views.RedefinirSenhaConcluidoView.as_view(),
        name="redefinir-senha-concluido",
    ),
    path(
        "redefinir-senha/<uidb64>/<token>/",
        views.RedefinirSenhaConfirmarView.as_view(),
        name="redefinir-senha-confirmar",
    ),
]
