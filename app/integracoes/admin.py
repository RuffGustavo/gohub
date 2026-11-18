import uuid

from django.contrib import admin, messages

from app.leads.admin import _SomenteLeitura

from .models import EventoIntegracao, LeadIntegracao
from .services import NadaAReprocessar, reprocessar_integracao


class _SemDadosDoLead:
    """O lead aparece só pelo id (o __str__ do lead é o nome, dado pessoal). Busca exata por
    chave, id externo ou id do lead; nunca por e-mail."""

    campos_busca = ["=id_externo"]
    search_help_text = "Busca exata por ID externo, chave de idempotência ou ID do lead."
    list_select_related = ["sistema"]
    list_per_page = 50
    show_full_result_count = False

    def get_search_fields(self, request):
        # "=lead__id" só com UUID válido (outro texto viraria erro no banco).
        try:
            uuid.UUID((request.GET.get("q") or "").strip())
        except ValueError:
            return self.campos_busca
        return [*self.campos_busca, "=lead__id"]


@admin.register(EventoIntegracao)
class EventoIntegracaoAdmin(_SemDadosDoLead, _SomenteLeitura, admin.ModelAdmin):
    campos_busca = ["=chave_idempotencia", "=id_externo"]
    search_fields = campos_busca  # habilita a caixa de busca; os campos efetivos vêm de get_search_fields
    list_display = ["recebido_em", "ocorrido_em", "sistema", "tipo_evento", "resultado", "lead_id"]
    list_filter = ["resultado", "sistema", "tipo_evento"]
    fields = ["lead_id", "sistema", "tipo_evento", "resultado", "chave_idempotencia", "id_externo", "mensagem_erro",
              "payload", "ocorrido_em", "recebido_em"]
    readonly_fields = fields
    ordering = ["-recebido_em", "-id"]


@admin.register(LeadIntegracao)
class LeadIntegracaoAdmin(_SemDadosDoLead, _SomenteLeitura, admin.ModelAdmin):
    search_fields = _SemDadosDoLead.campos_busca
    list_display = ["lead_id", "sistema", "status", "tentativas", "ultima_tentativa_em", "proxima_tentativa_em",
                    "ultimo_erro"]
    list_filter = ["status", "sistema"]
    fields = ["lead_id", "sistema", "status", "id_externo", "tentativas", "ultima_tentativa_em",
              "proxima_tentativa_em", "sucesso_em", "ultimo_erro", "created_at", "updated_at"]
    readonly_fields = fields
    ordering = ["-updated_at", "-id"]
    actions = ["reprocessar"]

    def has_reprocessar_permission(self, request):
        return request.user.has_perm("integracoes.reprocessar_integracao")

    @admin.action(description="Reprocessar notificação ao n8n", permissions=["reprocessar"])
    def reprocessar(self, request, queryset):
        total = ignoradas = 0
        for integracao in queryset.select_related("sistema")[:200]:
            try:
                reprocessar_integracao(integracao=integracao, usuario=request.user, request=request)
            except NadaAReprocessar:
                ignoradas += 1
            else:
                total += 1
        self.message_user(
            request, f"{total} integração(ões) reagendada(s); {ignoradas} sem nada a reprocessar.", messages.SUCCESS
        )
