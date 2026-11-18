from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse_lazy
from django.views.generic import DetailView, FormView, ListView, TemplateView
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .forms import FiltroLeadForm, LeadPublicoForm, ReclassificacaoForm
from .models import Lead
from .permissions import LeadPermissions
from .selectors import filtrar_leads, historico_classificacoes, historico_status, leads_ativos
from .serializers import (
    LeadCapturaSerializer,
    LeadClassificacaoSerializer,
    LeadDetailSerializer,
    LeadListSerializer,
    LeadStatusHistoricoSerializer,
    ReclassificacaoSerializer,
)
from .services import reclassificar_lead, registrar_lead
from .throttles import LeadPublicoThrottle, captacao_permitida

MENSAGEM_RECEBIDO = "Recebemos seus dados. Em breve entraremos em contato."
# O corpo de uma captação legítima tem menos de 1 KB.
CAPTURA_MAX_BYTES = 16 * 1024


def _dados_do_lead(dados):
    return {campo: dados[campo] for campo in ("nome", "email", "telefone", "empresa", "cargo")}


# ------------------------------------------------------------------ telas públicas
class LeadPublicoCreateView(FormView):
    """Formulário público de captação (P5). Mesma resposta (redirecionamento para
    "obrigado") para lead novo, e-mail já cadastrado ou robô (honeypot)."""

    template_name = "leads/formulario.html"
    form_class = LeadPublicoForm
    success_url = reverse_lazy("leads:obrigado")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["politica_url"] = settings.POLITICA_PRIVACIDADE_URL
        return contexto

    def post(self, request, *args, **kwargs):
        # Mesmo limite e contador da API (D1).
        if not captacao_permitida(request):
            return TemplateResponse(request, "leads/limite.html", status=429)
        form = self.get_form()
        if form.is_bot:
            return redirect(self.get_success_url())
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form):
        try:
            registrar_lead(**_dados_do_lead(form.cleaned_data), request=self.request)
        except DjangoValidationError:
            form.add_error(None, "Não foi possível enviar. Confira os dados e tente novamente.")
            return self.form_invalid(form)
        return super().form_valid(form)


class LeadObrigadoView(TemplateView):
    template_name = "leads/obrigado.html"


# ------------------------------------------------------------------ painel (restrito)
class PainelLeadListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Lista de leads ativos com filtros (GET) e paginação de 50."""

    permission_required = "leads.view_lead"
    template_name = "leads/painel/lista.html"
    context_object_name = "leads"
    paginate_by = 50

    def get_queryset(self):
        self.filtro = FiltroLeadForm(self.request.GET)
        return filtrar_leads(**self.filtro.filtros())

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        parametros = self.request.GET.copy()
        parametros.pop("page", None)
        contexto["filtro"] = self.filtro
        contexto["querystring"] = parametros.urlencode()
        return contexto


class PainelLeadDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """Detalhe com os históricos; excluído ou anonimizado responde 404."""

    permission_required = "leads.view_lead"
    template_name = "leads/painel/detalhe.html"
    context_object_name = "lead"

    def get_queryset(self):
        return leads_ativos()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["historico_status"] = historico_status(self.object)
        contexto["classificacoes"] = historico_classificacoes(self.object)
        contexto["pode_reclassificar"] = self.request.user.has_perm("leads.reclassificar_lead")
        contexto.setdefault("form_reclassificacao", ReclassificacaoForm())
        # P18: estado por sistema e últimos eventos, só para quem pode ver integrações (2 consultas).
        if self.request.user.has_perm("integracoes.view_leadintegracao"):
            from app.integracoes.selectors import integracoes_do_lead  # import tardio: integracoes depende de leads

            pode_ver_eventos = self.request.user.has_perm("integracoes.view_eventointegracao")
            contexto["integracoes"], contexto["eventos_integracao"] = integracoes_do_lead(
                self.object, com_eventos=pode_ver_eventos
            )
            contexto["pode_ver_integracoes"] = True
            contexto["pode_ver_eventos"] = pode_ver_eventos
        return contexto


class PainelReclassificarView(PainelLeadDetailView):
    """Reclassificação manual (só POST, com CSRF). Erro de validação reexibe o detalhe."""

    permission_required = ["leads.view_lead", "leads.reclassificar_lead"]
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = ReclassificacaoForm(request.POST)
        if form.is_valid():
            try:
                reclassificar_lead(lead=self.object, usuario=request.user, request=request, **form.cleaned_data)
            except (DjangoValidationError, IntegrityError):
                form.add_error(None, "Não foi possível reclassificar este lead. Recarregue a página e tente novamente.")
            else:
                messages.success(request, "Lead reclassificado. O histórico anterior foi mantido.")
                return redirect("leads:painel-detalhe", pk=self.object.pk)
        return self.render_to_response(self.get_context_data(form_reclassificacao=form), status=400)


# ------------------------------------------------------------------ API
class LeadCapturaAPIView(APIView):
    """Recebe os dados do formulário de contato.

    Responde sempre com a mesma mensagem quando os dados são válidos.
    """

    # Público (P5): sem sessão, logo sem CSRF e sem ligação com usuários logados.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LeadPublicoThrottle]
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # o throttle conta antes de qualquer validação
        try:
            tamanho = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            tamanho = 0
        if tamanho > CAPTURA_MAX_BYTES:
            raise exceptions.ValidationError({"detail": "Requisição grande demais."})

    def post(self, request):
        dados = request.data if isinstance(request.data, dict) else {}
        if str(dados.get("website") or "").strip():  # honeypot: mesma resposta, nada gravado
            return Response({"detail": MENSAGEM_RECEBIDO}, status=status.HTTP_202_ACCEPTED)
        serializer = LeadCapturaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            registrar_lead(**_dados_do_lead(serializer.validated_data), request=request)
        except DjangoValidationError:
            raise exceptions.ValidationError({"detail": "Dados inválidos."}) from None
        return Response({"detail": MENSAGEM_RECEBIDO}, status=status.HTTP_202_ACCEPTED)


class LeadCursorPagination(CursorPagination):
    """Paginação por cursor (keyset) sobre o índice ix_lead_listagem (P12)."""

    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"
    ordering = ("-created_at", "-id")


class LeadViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Consulta de leads (somente leitura) e reclassificação manual.

    Filtros da lista: status, classificacao (frio, morno, quente ou sem), inicio e fim
    (AAAA-MM-DD) e q (busca por nome, e-mail ou empresa).
    """

    queryset = Lead.objects.ativos()  # exigido por DjangoModelPermissions
    permission_classes = [LeadPermissions]
    permissoes_por_acao = {"reclassificar": ["leads.reclassificar_lead"]}
    pagination_class = LeadCursorPagination
    lookup_value_regex = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

    def get_queryset(self):
        # A checagem de permissão (DjangoModelPermissions) chama este método antes da view:
        # aqui não se valida nada, para que sem permissão a resposta seja 403, não 400.
        if self.action == "list":
            return filtrar_leads(**FiltroLeadForm(self.request.query_params).filtros())
        return leads_ativos()

    def list(self, request, *args, **kwargs):
        filtro = FiltroLeadForm(request.query_params)
        if not filtro.is_valid():
            raise exceptions.ValidationError(filtro.errors)
        return super().list(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.action == "list":
            return LeadListSerializer
        if self.action == "reclassificar":
            return ReclassificacaoSerializer
        return LeadDetailSerializer

    @action(detail=True, methods=["get"])
    def historico(self, request, pk=None):
        """Histórico de status e de classificações do lead (mais recentes primeiro)."""
        lead = self.get_object()
        return Response({
            "status": LeadStatusHistoricoSerializer(historico_status(lead), many=True).data,
            "classificacoes": LeadClassificacaoSerializer(historico_classificacoes(lead), many=True).data,
        })

    @action(detail=True, methods=["post"])
    def reclassificar(self, request, pk=None):
        """Reclassifica o lead manualmente; a classificação anterior fica no histórico."""
        lead = self.get_object()
        serializer = ReclassificacaoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            registro = reclassificar_lead(lead=lead, usuario=request.user, request=request, **serializer.validated_data)
        except IntegrityError:
            raise exceptions.NotFound() from None
        except DjangoValidationError as exc:
            raise exceptions.ValidationError(exc.messages) from None
        return Response(LeadClassificacaoSerializer(registro).data, status=status.HTTP_201_CREATED)
