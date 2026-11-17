from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.template.response import TemplateResponse

from .models import Lead, LeadClassificacao, LeadStatusHistorico, Sistema
from .services import anonimizar_lead


class _SomenteLeitura:
    """Restringe (nunca amplia): o admin só exibe. Históricos são append-only no banco e os
    dados do lead não são editáveis (P10); leads entram pelo formulário público."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LeadStatusHistoricoInline(_SomenteLeitura, admin.TabularInline):
    model = LeadStatusHistorico
    fields = ["ocorrido_em", "status_anterior", "status_novo", "sistema", "usuario", "observacao"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sistema", "usuario")


class LeadClassificacaoInline(_SomenteLeitura, admin.TabularInline):
    model = LeadClassificacao
    fields = ["classificado_em", "classificacao", "metodo", "confianca", "versao_criterio", "sistema", "usuario",
              "justificativa"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sistema", "usuario")


@admin.register(Lead)
class LeadAdmin(_SomenteLeitura, admin.ModelAdmin):
    # Sem e-mail/telefone na lista (dados pessoais só no detalhe); busca por e-mail é exata
    # o bastante para atender pedidos do titular.
    list_display = ["nome", "empresa", "cargo", "created_at", "classificacao_atual", "status_atual"]
    list_filter = ["status_atual", "classificacao_atual", "created_at"]
    search_fields = ["nome", "email", "empresa"]
    list_per_page = 50
    show_full_result_count = False
    ordering = ["-created_at", "-id"]
    fieldsets = [
        ("Dados informados", {"fields": ["nome", "email", "telefone", "empresa", "cargo"]}),
        ("Situação", {"fields": ["status_atual", "status_atualizado_em", "classificacao_atual", "classificado_em"]}),
        ("Consentimento (LGPD)", {"fields": ["consentimento_em", "politica_versao"]}),
        ("Controle", {"fields": ["id", "created_at", "updated_at"]}),
    ]
    readonly_fields = [campo for _, opcoes in fieldsets for campo in opcoes["fields"]]
    inlines = [LeadClassificacaoInline, LeadStatusHistoricoInline]
    actions = ["anonimizar"]

    def get_queryset(self, request):
        # Excluídos e anonimizados não aparecem (o histórico deles continua nos admins próprios).
        return super().get_queryset(request).filter(deleted_at__isnull=True)

    def has_anonimizar_permission(self, request):
        return request.user.has_perm("leads.anonimizar_lead")

    @admin.action(description="Anonimizar dados pessoais (LGPD)", permissions=["anonimizar"])
    def anonimizar(self, request, queryset):
        # Irreversível: exige a página de confirmação (POST com "confirmar").
        if request.POST.get("confirmar") != "sim":
            contexto = {
                **self.admin_site.each_context(request),
                "title": "Confirmar anonimização",
                "opts": self.model._meta,
                "leads": queryset,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
            }
            return TemplateResponse(request, "admin/leads/lead/confirmar_anonimizacao.html", contexto)
        total = 0
        for lead in queryset:
            try:
                anonimizar_lead(lead=lead, usuario=request.user, request=request)
            except ValidationError:
                continue
            total += 1
        self.message_user(request, f"{total} lead(s) anonimizado(s).", messages.SUCCESS)
        return None


class _HistoricoAdmin(_SomenteLeitura, admin.ModelAdmin):
    """Histórico mostra o lead (nome) e texto livre: exige também leads.view_lead, para que
    a permissão de ver só o histórico não contorne a leitura de leads. Busca por e-mail
    exata (sem icontains, que permitiria descobrir e-mails caractere a caractere)."""

    search_fields = ["=lead__email"]
    search_help_text = "Busca pelo e-mail exato do lead."
    list_select_related = ["sistema", "usuario"]
    list_per_page = 50
    show_full_result_count = False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("leads.view_lead") and super().has_view_permission(request, obj)

    def has_module_permission(self, request):
        return request.user.has_perm("leads.view_lead") and super().has_module_permission(request)


@admin.register(LeadStatusHistorico)
class LeadStatusHistoricoAdmin(_HistoricoAdmin):
    list_display = ["lead_id", "status_anterior", "status_novo", "sistema", "usuario", "ocorrido_em"]
    list_filter = ["status_novo", "sistema"]
    fields = ["lead", "status_anterior", "status_novo", "sistema", "usuario", "observacao", "ocorrido_em", "created_at"]


@admin.register(LeadClassificacao)
class LeadClassificacaoAdmin(_HistoricoAdmin):
    list_display = ["lead_id", "classificacao", "metodo", "sistema", "usuario", "classificado_em"]
    list_filter = ["classificacao", "metodo"]
    fields = ["lead", "classificacao", "metodo", "confianca", "versao_criterio", "sistema", "usuario", "justificativa",
              "classificado_em", "created_at"]


@admin.register(Sistema)
class SistemaAdmin(_SomenteLeitura, admin.ModelAdmin):
    # Códigos são usados pelos gatilhos (ex.: aplicacao_web): alteração só por migration.
    list_display = ["codigo", "nome", "ativo"]
    list_filter = ["ativo"]
    search_fields = ["codigo", "nome"]
