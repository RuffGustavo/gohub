from django.contrib import admin, messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group

from .forms import UsuarioChangeForm, UsuarioCreationForm, UsuarioPasswordChangeForm
from .models import RegistroAcesso, Usuario

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
        # Restringe (não amplia), R2: só superusuário altera conta com privilégio (superusuário,
        # grupo, permissão direta ou acesso ao admin), inclusive a própria. Vale para a tela de
        # alteração, a troca de senha (user_change_password) e a ação de exclusão lógica.
        if obj is not None and not request.user.is_superuser and obj.tem_privilegio():
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and obj.pk == request.user.pk:
            # A troca do próprio e-mail muda o hash da sessão: mantém só a sessão atual.
            update_session_auth_hash(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Sem exclusão física: históricos referenciam o usuário (ON DELETE RESTRICT).
        # Use a ação "Excluir logicamente".
        return False

    @admin.action(description="Excluir logicamente os usuários selecionados", permissions=["change"])
    def excluir_logicamente(self, request, queryset):
        alvos = queryset.filter(deleted_at__isnull=True).exclude(pk=request.user.pk)
        if not request.user.is_superuser:
            alvos = alvos.sem_privilegio()
        total = 0
        for usuario in alvos:
            usuario.excluir()
            total += 1
        ignorados = queryset.count() - total
        self.message_user(request, f"{total} usuário(s) excluído(s) logicamente.", messages.SUCCESS)
        if ignorados:
            self.message_user(
                request,
                f"{ignorados} ignorado(s): já excluídos, a própria conta ou contas com privilégio.",
                messages.WARNING,
            )


# Grupos: quem altera um grupo concede as permissões dele a todos os membros, inclusive a si
# mesmo. Por isso criar, alterar e excluir grupos é exclusivo de superusuários (R1); os demais
# só consultam, com view_group. O model Permission não é registrado no admin: ninguém o edita.
admin.site.unregister(Group)


@admin.register(Group)
class GrupoAdmin(GroupAdmin):
    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)


@admin.register(RegistroAcesso)
class RegistroAcessoAdmin(admin.ModelAdmin):
    """Consulta do histórico de acessos (view_registroacesso). Somente leitura: os registros
    são gravados pelos sinais de autenticação e nunca alterados nem apagados."""

    list_display = ["ocorrido_em", "usuario", "status", "canal", "ip", "navegador", "dispositivo"]
    list_filter = ["status", "canal", "dispositivo", "ocorrido_em"]
    search_fields = ["usuario__username", "ip"]
    list_select_related = ["usuario"]
    date_hierarchy = "ocorrido_em"
    readonly_fields = [
        "usuario", "status", "canal", "ip", "navegador", "sistema_operacional", "dispositivo", "ocorrido_em",
    ]
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
