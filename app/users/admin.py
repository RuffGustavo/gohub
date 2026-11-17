from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

from .forms import UsuarioChangeForm, UsuarioCreationForm, UsuarioPasswordChangeForm
from .models import Usuario

# Só um superusuário concede privilégios; sem isso, quem tem "change_usuario"
# poderia se tornar superusuário ou se atribuir qualquer permissão.
CAMPOS_DE_PRIVILEGIO = ["is_superuser", "groups", "user_permissions"]


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    form = UsuarioChangeForm
    add_form = UsuarioCreationForm
    change_password_form = UsuarioPasswordChangeForm
    list_display = ["username", "nome", "email", "is_active", "is_superuser", "last_login"]
    list_filter = ["is_active", "is_superuser", "groups"]
    search_fields = ["username", "nome", "email"]
    ordering = ["nome"]
    readonly_fields = ["last_login", "created_at", "updated_at", "deleted_at"]
    actions = ["excluir_logicamente"]
    fieldsets = [
        (None, {"fields": ["username", "password"]}),
        ("Dados pessoais", {"fields": ["nome", "email"]}),
        ("Permissões", {"fields": ["is_active", "is_superuser", "groups", "user_permissions"]}),
        ("Auditoria", {"fields": ["last_login", "created_at", "updated_at", "deleted_at"]}),
    ]
    add_fieldsets = [
        (None, {"classes": ["wide"], "fields": ["username", "email", "nome", "password1", "password2"]}),
    ]

    def get_readonly_fields(self, request, obj=None):
        campos = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            campos += CAMPOS_DE_PRIVILEGIO
        return campos

    def has_change_permission(self, request, obj=None):
        # Restringe (não amplia): só superusuário altera outro superusuário.
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Sem exclusão física: históricos referenciam o usuário (ON DELETE RESTRICT).
        # Use a ação "Excluir logicamente".
        return False

    @admin.action(description="Excluir logicamente os usuários selecionados", permissions=["change"])
    def excluir_logicamente(self, request, queryset):
        alvos = queryset.filter(deleted_at__isnull=True).exclude(pk=request.user.pk)
        if not request.user.is_superuser:
            alvos = alvos.filter(is_superuser=False)
        total = 0
        for usuario in alvos:
            usuario.excluir()
            total += 1
        ignorados = queryset.count() - total
        self.message_user(request, f"{total} usuário(s) excluído(s) logicamente.", messages.SUCCESS)
        if ignorados:
            self.message_user(
                request,
                f"{ignorados} ignorado(s): já excluídos, a própria conta ou superusuários.",
                messages.WARNING,
            )
