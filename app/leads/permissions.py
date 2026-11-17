from rest_framework.permissions import DjangoModelPermissions


class LeadPermissions(DjangoModelPermissions):
    """DjangoModelPermissions que também exige `view_<model>` para ler (o padrão deixa
    GET/HEAD/OPTIONS livres para qualquer autenticado) e permissões extras por ação.

    POST não significa "criar lead" aqui (não há create): é usado só por ações, que exigem
    `view_lead` + o que a view declarar em `permissoes_por_acao`.
    """

    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": ["%(app_label)s.view_%(model_name)s"],
        "POST": ["%(app_label)s.view_%(model_name)s"],
    }

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        extras = getattr(view, "permissoes_por_acao", {}).get(getattr(view, "action", None), [])
        return request.user.has_perms(extras)
