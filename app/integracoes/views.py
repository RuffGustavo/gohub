"""Views das integrações: API interna (n8n), API do painel e telas do painel.

API interna: só token do sistema + assinatura HMAC + rede (authentication/permissions deste
app); nunca sessão. API do painel e telas: sessão + permissões explícitas (decisão P12).
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from app.leads.models import Lead, Sistema
from app.leads.permissions import LeadPermissions

from . import services
from .authentication import SistemaTokenAuthentication
from .forms import FiltroEventoForm, FiltroFilaForm, FiltroIntegracaoForm
from .models import EventoIntegracao, LeadIntegracao
from .permissions import AssinaturaHMACValida, IsSistemaIntegrado, RedePermitida
from .selectors import eventos_ativos, filtrar_eventos, filtrar_integracoes, integracoes_ativas, resumo_por_sistema
from .serializers import (
    EventoEntradaSerializer,
    EventoIntegracaoDetalheSerializer,
    EventoIntegracaoSerializer,
    LeadIntegracaoSerializer,
    LeadInternoSerializer,
)
from .throttles import InternoSistemaThrottle

CORPO_MAX_BYTES = 16 * 1024
CAMPOS_LEAD_INTERNO = [
    "id", "nome", "email", "telefone", "empresa", "cargo", "created_at", "status_atual", "classificacao_atual",
]
PERM_VER_INTEGRACOES = "integracoes.view_leadintegracao"
PERM_VER_EVENTOS = "integracoes.view_eventointegracao"
PERM_REPROCESSAR = "integracoes.reprocessar_integracao"
PERM_GERENCIAR = "integracoes.gerenciar_integracoes"


class Conflito(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflito com um registro existente."
    default_code = "conflito"


class CorpoGrandeDemais(exceptions.APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "Requisição grande demais."
    default_code = "corpo_grande_demais"


# ------------------------------------------------------------------ API interna (n8n)
class _InternoBase(APIView):
    """Base das rotas /api/interno/: fora do router e do Swagger (decisão P1)."""

    authentication_classes = [SistemaTokenAuthentication]
    # RedePermitida roda antes, em initial(): fora da rede a resposta é 403 com ou sem token
    # válido (não serve para confirmar um token vazado).
    permission_classes = [IsSistemaIntegrado, AssinaturaHMACValida]
    throttle_classes = [InternoSistemaThrottle]
    parser_classes = [JSONParser]
    swagger_schema = None

    def initial(self, request, *args, **kwargs):
        # Antes da assinatura (que lê o corpo bruto): corpo grande recusado sem ser lido.
        try:
            tamanho = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            tamanho = CORPO_MAX_BYTES + 1
        if tamanho > CORPO_MAX_BYTES:
            raise CorpoGrandeDemais()
        RedePermitida().has_permission(request, self)  # levanta PermissionDenied (403)
        super().initial(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def lead_ativo(self, pk, campos=None):
        """Lead excluído/anonimizado ou inexistente: 404 (PR7, antes da idempotência)."""
        qs = Lead.objects.ativos()
        if campos:
            qs = qs.only(*campos)
        lead = qs.filter(pk=pk).first()
        if lead is None:
            raise exceptions.NotFound()
        return lead


class LeadInternoView(_InternoBase):
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        lead = self.lead_ativo(pk, CAMPOS_LEAD_INTERNO)
        return Response(LeadInternoSerializer(lead).data)


class LeadEventosInternoView(_InternoBase):
    http_method_names = ["post", "options"]

    def post(self, request, pk):
        lead = self.lead_ativo(pk, ["id", "created_at"])
        serializer = EventoEntradaSerializer(data=request.data, context={"sistema": request.auth, "lead": lead})
        serializer.is_valid(raise_exception=True)
        try:
            evento, criado = services.registrar_evento_recebido(
                sistema=request.auth, lead=lead, dados=serializer.validated_data
            )
        except services.ConflitoIdempotencia:
            raise Conflito("Chave de idempotência já usada para outro lead.") from None
        except services.ConflitoIdExterno:
            raise Conflito("ID externo já vinculado a outro lead.") from None
        except services.LeadIndisponivel:
            raise exceptions.NotFound() from None
        except services.ErroGravacao:
            raise exceptions.ValidationError({"detail": "Não foi possível registrar o evento."}) from None
        return Response(
            EventoIntegracaoSerializer(evento).data, status=status.HTTP_201_CREATED if criado else status.HTTP_200_OK
        )


# ------------------------------------------------------------------ API do painel
class IntegracaoCursorPagination(CursorPagination):
    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"
    ordering = ("-updated_at", "-id")


class EventoCursorPagination(IntegracaoCursorPagination):
    ordering = ("-recebido_em", "-id")


class LeadIntegracaoViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Estado das integrações dos leads (somente leitura) e reprocessamento.

    Filtros da lista: status (pode repetir), sistema e lead.
    """

    queryset = LeadIntegracao.objects.filter(lead__deleted_at__isnull=True)  # exigido por DjangoModelPermissions
    serializer_class = LeadIntegracaoSerializer
    permission_classes = [LeadPermissions]
    permissoes_por_acao = {"reprocessar": [PERM_REPROCESSAR]}
    pagination_class = IntegracaoCursorPagination
    lookup_value_regex = "[0-9]+"

    def get_queryset(self):
        if self.action == "list":
            return filtrar_integracoes(**FiltroIntegracaoForm(self.request.query_params).filtros())
        return integracoes_ativas()

    def list(self, request, *args, **kwargs):
        filtro = FiltroIntegracaoForm(request.query_params)
        if not filtro.is_valid():
            raise exceptions.ValidationError(filtro.errors)
        return super().list(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def reprocessar(self, request, pk=None):
        """Agenda nova tentativa de notificação."""
        integracao = self.get_object()
        try:
            atual = services.reprocessar_integracao(integracao=integracao, usuario=request.user, request=request)
        except services.NadaAReprocessar:
            raise Conflito("Nada a reprocessar.") from None
        return Response(LeadIntegracaoSerializer(atual).data, status=status.HTTP_202_ACCEPTED)


class EventoIntegracaoViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Log de eventos de integração (somente leitura).

    Filtros: sistema, resultado, tipo_evento, lead, inicio e fim (AAAA-MM-DD).
    """

    queryset = EventoIntegracao.objects.filter(lead__deleted_at__isnull=True)
    permission_classes = [LeadPermissions]
    pagination_class = EventoCursorPagination
    lookup_value_regex = "[0-9]+"

    def get_queryset(self):
        if self.action == "list":
            return filtrar_eventos(**FiltroEventoForm(self.request.query_params).filtros())
        return eventos_ativos()

    def get_serializer_class(self):
        return EventoIntegracaoDetalheSerializer if self.action == "retrieve" else EventoIntegracaoSerializer

    def list(self, request, *args, **kwargs):
        filtro = FiltroEventoForm(request.query_params)
        if not filtro.is_valid():
            raise exceptions.ValidationError(filtro.errors)
        return super().list(request, *args, **kwargs)


# ------------------------------------------------------------------ telas do painel
def _querystring(request):
    parametros = request.GET.copy()
    parametros.pop("page", None)
    return parametros.urlencode()


def _destino_seguro(request, padrao):
    destino = request.POST.get("next") or ""
    if destino and url_has_allowed_host_and_scheme(
        destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return destino
    return padrao


class PainelVisaoGeralView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = PERM_VER_INTEGRACOES
    template_name = "integracoes/painel/visao_geral.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["sistemas"] = resumo_por_sistema()
        contexto["credenciais_configuradas"] = services.credenciais_configuradas()
        contexto["notificacao_configurada"] = services.notificacao_configurada()
        contexto["pode_gerenciar"] = self.request.user.has_perm(PERM_GERENCIAR)
        contexto["codigo_n8n"] = Sistema.N8N
        contexto["codigo_aplicacao_web"] = Sistema.APLICACAO_WEB
        return contexto


class PainelFilaView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = PERM_VER_INTEGRACOES
    template_name = "integracoes/painel/fila.html"
    context_object_name = "integracoes"
    paginate_by = 50

    def get_queryset(self):
        self.filtro = FiltroFilaForm(self.request.GET)
        return filtrar_integracoes(**self.filtro.filtros())

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["filtro"] = self.filtro
        contexto["querystring"] = _querystring(self.request)
        contexto["pode_reprocessar"] = self.request.user.has_perm(PERM_REPROCESSAR)
        contexto["codigo_n8n"] = Sistema.N8N
        return contexto


class PainelReprocessarView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = [PERM_VER_INTEGRACOES, PERM_REPROCESSAR]
    http_method_names = ["post"]

    def post(self, request, pk):
        integracao = get_object_or_404(integracoes_ativas(), pk=pk)
        try:
            services.reprocessar_integracao(integracao=integracao, usuario=request.user, request=request)
        except services.NadaAReprocessar:
            messages.warning(request, "Nada a reprocessar: a integração está em andamento, concluída ou é "
                                      "reprocessada pelo n8n.")
        else:
            messages.success(request, "Nova tentativa agendada.")
        return redirect(_destino_seguro(request, "integracoes:painel-fila"))


class PainelEventosView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = PERM_VER_EVENTOS
    template_name = "integracoes/painel/eventos.html"
    context_object_name = "eventos"
    paginate_by = 50

    def get_queryset(self):
        self.filtro = FiltroEventoForm(self.request.GET)
        return filtrar_eventos(**self.filtro.filtros())

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["filtro"] = self.filtro
        contexto["querystring"] = _querystring(self.request)
        return contexto


class PainelEventoDetalheView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = PERM_VER_EVENTOS
    template_name = "integracoes/painel/evento_detalhe.html"
    context_object_name = "evento"

    def get_queryset(self):
        return eventos_ativos()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        payload = self.object.payload if isinstance(self.object.payload, dict) else {}
        contexto["payload_itens"] = sorted(payload.items())
        return contexto


class PainelAlternarSistemaView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = PERM_GERENCIAR
    http_method_names = ["post"]

    def post(self, request, pk):
        sistema = get_object_or_404(Sistema, pk=pk)
        try:
            atual = services.alternar_sistema(sistema=sistema, usuario=request.user, request=request)
        except DjangoValidationError:
            messages.error(request, "A aplicação web não pode ser desativada.")
        else:
            estado = "ativado" if atual.ativo else "desativado"
            messages.success(request, f"Sistema {atual.nome} {estado}.")
        return redirect("integracoes:painel-visao-geral")


class PainelTestarConexaoView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = PERM_GERENCIAR
    http_method_names = ["post"]

    def post(self, request, pk):
        sistema = get_object_or_404(Sistema, pk=pk, codigo=Sistema.N8N)
        try:
            ok, codigo = services.testar_conexao(sistema=sistema, usuario=request.user, request=request)
        except services.LimiteTesteConexao:
            messages.warning(request, "Muitos testes seguidos. Aguarde um minuto para testar de novo.")
            return redirect("integracoes:painel-visao-geral")
        if ok:
            messages.success(request, "Conexão com o n8n confirmada.")
        elif codigo == "nao_configurado":
            messages.warning(request, "O webhook do n8n não está configurado.")
        else:
            messages.error(request, f"Falha na conexão com o n8n (código: {codigo}).")
        return redirect("integracoes:painel-visao-geral")
