from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from .limite_login import bloqueado, marcar_bloqueio


class ModelBackendComLimite(ModelBackend):
    """ModelBackend que recusa a tentativa, sem verificar a senha, quando o IP ou a conta
    neste IP excedeu o limite de falhas (app/users/limite_login.py).

    PermissionDenied faz o django.contrib.auth.authenticate parar (nenhum outro backend é
    tentado) e disparar user_login_failed; o receptor reconhece o bloqueio e não grava.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if bloqueado(request, username):
            marcar_bloqueio(request)
            raise PermissionDenied
        return super().authenticate(request, username=username, password=password, **kwargs)
