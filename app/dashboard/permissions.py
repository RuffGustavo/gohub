"""Quem vê o quê nos dashboards (decidido no backend, por seção).

- Dashboard pessoal: qualquer usuário autenticado, sempre sobre a própria conta
  (request.user); nenhum parâmetro escolhe outro usuário.
- Dashboard gerencial: cada seção exige a permissão de leitura do dado que agrega. Quem não
  tem nenhuma recebe 403. Superusuário tem todas (has_perm).
"""

from rest_framework.permissions import BasePermission

SECOES_GERENCIAIS = {
    "leads": "leads.view_lead",
    "usuarios": "users.view_usuario",
    "acessos": "users.view_registroacesso",
}


def secoes_permitidas(usuario):
    if not (usuario and usuario.is_authenticated and usuario.is_active):
        return []
    return [secao for secao, perm in SECOES_GERENCIAIS.items() if usuario.has_perm(perm)]


def pode_ver_gerencial(usuario):
    return bool(secoes_permitidas(usuario))


class PodeVerGerencial(BasePermission):
    message = "Você não tem permissão para ver o painel gerencial."

    def has_permission(self, request, view):
        return pode_ver_gerencial(request.user)
