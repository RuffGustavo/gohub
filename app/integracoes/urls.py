from django.urls import path

from . import views

app_name = "integracoes"

urlpatterns = [
    # Painel (exige sessão e as permissões de integrações).
    path("painel/integracoes/", views.PainelVisaoGeralView.as_view(), name="painel-visao-geral"),
    path("painel/integracoes/fila/", views.PainelFilaView.as_view(), name="painel-fila"),
    path(
        "painel/integracoes/fila/<int:pk>/reprocessar/",
        views.PainelReprocessarView.as_view(),
        name="painel-reprocessar",
    ),
    path("painel/integracoes/eventos/", views.PainelEventosView.as_view(), name="painel-eventos"),
    path(
        "painel/integracoes/eventos/<int:pk>/",
        views.PainelEventoDetalheView.as_view(),
        name="painel-evento-detalhe",
    ),
    path(
        "painel/integracoes/sistemas/<int:pk>/alternar/",
        views.PainelAlternarSistemaView.as_view(),
        name="painel-sistema-alternar",
    ),
    path(
        "painel/integracoes/sistemas/<int:pk>/testar/",
        views.PainelTestarConexaoView.as_view(),
        name="painel-sistema-testar",
    ),
]
