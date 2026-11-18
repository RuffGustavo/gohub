from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView
from rest_framework import exceptions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.leads.forms import FiltroLeadForm
from app.leads.selectors import CAMPOS_LISTAGEM, filtrar_leads
from app.users.models import RegistroAcesso

from . import metricas
from .forms import FiltroAcessoForm, FiltroHistoricoForm
from .periodos import FiltroPeriodoForm
from .permissions import PodeVerGerencial, pode_ver_gerencial, secoes_permitidas

POR_PAGINA = 20
CAMPOS_PERIODO = ("periodo", "inicio", "fim", "granularidade")


def _paginar(request, queryset, parametro):
    return Paginator(queryset, POR_PAGINA).get_page(request.GET.get(parametro))


def _querystring(request, *, sem=(), so=None):
    """Querystring atual sem os parâmetros `sem` (ou só com os de `so`), para links e para a API."""
    itens = [
        (chave, valor)
        for chave, valores in request.GET.lists()
        for valor in valores
        if chave not in sem and (so is None or chave in so)
    ]
    return urlencode(itens)


PAGINAS = ("pagina", "pagina_leads", "pagina_acessos")
CAMPOS_LEADS = ("status", "classificacao", "q")
CAMPOS_ACESSOS = ("acesso-status", "acesso-canal", "acesso-usuario")


def _ocultos(request, campos_do_form):
    """Pares (nome, valor) dos outros filtros, repetidos como <input type="hidden"> em cada
    formulário da página para que filtrar uma tabela não apague o filtro das demais."""
    return [
        (chave, valor)
        for chave, valores in request.GET.lists()
        for valor in valores
        if chave not in campos_do_form and chave not in PAGINAS
        and chave in CAMPOS_PERIODO + CAMPOS_LEADS + CAMPOS_ACESSOS
    ]


def _url_api(nome, request):
    query = _querystring(request, so=CAMPOS_PERIODO)
    return f"{reverse(nome)}?{query}" if query else reverse(nome)


# ------------------------------------------------------------------ telas
@method_decorator(never_cache, name="dispatch")
class PainelPessoalView(LoginRequiredMixin, TemplateView):
    """Dashboard do próprio usuário (destino após o login). Tudo é filtrado por request.user:
    não há id na URL nem parâmetro que escolha outra conta."""

    template_name = "users/painel.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usuario = self.request.user
        filtro = FiltroPeriodoForm(self.request.GET)
        periodo = filtro.periodo_escolhido()
        filtro_historico = FiltroHistoricoForm(self.request.GET)
        historico = RegistroAcesso.objects.filter(usuario=usuario, **periodo.filtro("ocorrido_em"))
        status = filtro_historico.filtros()["status"]
        if status:
            historico = historico.filter(status=status)
        login_atual, acesso_anterior, ultima_falha = metricas.ultimos_acessos(usuario)
        contexto.update(
            filtro=filtro,
            filtro_historico=filtro_historico,
            periodo=periodo,
            login_atual=login_atual,
            acesso_anterior=acesso_anterior,
            ultima_falha=ultima_falha,
            historico=_paginar(self.request, historico.order_by("-ocorrido_em", "-id"), "pagina"),
            querystring=_querystring(self.request, sem=("pagina",)),
            url_api=_url_api("dashboard-pessoal", self.request),
            pode_ver_gerencial=pode_ver_gerencial(usuario),
        )
        return contexto


@method_decorator(never_cache, name="dispatch")
class PainelGerencialView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Dashboard gerencial. Anônimo → login; autenticado sem nenhuma permissão de leitura
    (leads.view_lead, users.view_usuario, users.view_registroacesso) → 403. Cada seção só é
    montada para quem tem a permissão correspondente."""

    template_name = "dashboard/gerencial.html"

    def test_func(self):
        return pode_ver_gerencial(self.request.user)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        request = self.request
        secoes = secoes_permitidas(request.user)
        filtro = FiltroPeriodoForm(request.GET)
        periodo = filtro.periodo_escolhido()
        contexto.update(
            filtro=filtro,
            periodo=periodo,
            secoes=secoes,
            url_api=_url_api("dashboard-gerencial", request),
            ocultos_periodo=_ocultos(request, CAMPOS_PERIODO),
            ocultos_leads=_ocultos(request, CAMPOS_LEADS),
            ocultos_acessos=_ocultos(request, CAMPOS_ACESSOS),
            indisponivel_origem=metricas.INDISPONIVEL_ORIGEM,
            indisponivel_responsavel=metricas.INDISPONIVEL_RESPONSAVEL,
        )

        if "leads" in secoes:
            filtro_leads = FiltroLeadForm(request.GET)
            opcoes = filtro_leads.filtros()
            leads = filtrar_leads(
                status=opcoes["status"], classificacao=opcoes["classificacao"], busca=opcoes["busca"],
                inicio=periodo.inicio, fim=periodo.fim,
            ).only(*CAMPOS_LISTAGEM, "updated_at")
            erros = {campo: filtro_leads.errors[campo] for campo in ("status", "classificacao", "q") if campo in filtro_leads.errors}
            contexto.update(
                filtro_leads=filtro_leads,
                filtro_leads_erros=erros,
                leads=_paginar(request, leads, "pagina_leads"),
                querystring_leads=_querystring(request, sem=("pagina_leads",)),
            )

        if "acessos" in secoes:
            filtro_acessos = FiltroAcessoForm(request.GET, prefix="acesso")
            opcoes = filtro_acessos.filtros()
            acessos = RegistroAcesso.objects.select_related("usuario").filter(**periodo.filtro("ocorrido_em"))
            if opcoes["status"]:
                acessos = acessos.filter(status=opcoes["status"])
            if opcoes["canal"]:
                acessos = acessos.filter(canal=opcoes["canal"])
            if opcoes["usuario"]:
                acessos = acessos.filter(usuario__username__icontains=opcoes["usuario"])
            acessos = acessos.only(
                "ocorrido_em", "status", "canal", "ip", "navegador", "sistema_operacional", "dispositivo",
                "usuario__username", "usuario__nome",
            ).order_by("-ocorrido_em", "-id")
            contexto.update(
                filtro_acessos=filtro_acessos,
                acessos=_paginar(request, acessos, "pagina_acessos"),
                querystring_acessos=_querystring(request, sem=("pagina_acessos",)),
            )
        return contexto


# ------------------------------------------------------------------ API (alimenta os gráficos)
class DashboardViewSet(viewsets.GenericViewSet):
    """Indicadores e séries dos dashboards (somente leitura).

    Parâmetros: periodo (7d, 30d, 90d, 12m ou personalizado), inicio e fim (AAAA-MM-DD, só no
    personalizado) e granularidade (dia, semana ou mes).
    """

    # Sem queryset: as consultas são agregações escopadas no código (DjangoModelPermissions não
    # se aplica); cada ação declara a própria permissão.
    permission_classes = [IsAuthenticated]
    pagination_class = None
    # Sem corpo de entrada (só GET); serializer vazio só para a geração do Swagger.
    serializer_class = serializers.Serializer

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        add_never_cache_headers(response)  # dados pessoais e gerenciais: nada de cache intermediário
        return response

    def _periodo(self):
        filtro = FiltroPeriodoForm(self.request.query_params)
        if not filtro.is_valid():
            raise exceptions.ValidationError(filtro.errors)
        return filtro.periodo_escolhido()

    @action(detail=False, methods=["get"])
    def pessoal(self, request):
        """Acessos da conta autenticada. Não aceita escolher outro usuário."""
        return Response(metricas.resumo_pessoal(request.user, self._periodo()))

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated, PodeVerGerencial])
    def gerencial(self, request):
        """Indicadores consolidados; cada seção só aparece para quem tem a permissão de leitura dela."""
        periodo = self._periodo()
        secoes = secoes_permitidas(request.user)
        dados = {"periodo": periodo.como_dict(), "secoes": secoes}
        if "leads" in secoes:
            dados["leads"] = metricas.resumo_leads(periodo)
        if "usuarios" in secoes:
            dados["usuarios"] = {"indicadores": metricas.resumo_usuarios()}
        if "acessos" in secoes:
            dados["acessos"] = metricas.resumo_acessos(periodo)
        return Response(dados)
