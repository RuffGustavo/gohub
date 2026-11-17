from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .forms import LoginForm
from .serializers import UsuarioSerializer


class LoginView(auth_views.LoginView):
    template_name = "users/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    # Django 5: logout só por POST (com CSRF).
    next_page = reverse_lazy("users:login")


class UsuarioViewSet(viewsets.GenericViewSet):
    """Dados da conta autenticada."""

    serializer_class = UsuarioSerializer
    # Expõe apenas o próprio usuário, então não exige permissão de model
    # (DjangoModelPermissions dependeria de um queryset de todos os usuários).
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(self.get_serializer(request.user).data)
