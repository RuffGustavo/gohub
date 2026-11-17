from django.urls import path

from . import views

app_name = "leads"

urlpatterns = [
    # Públicas (P5, P11).
    path("leads/novo/", views.LeadPublicoCreateView.as_view(), name="formulario"),
    path("leads/obrigado/", views.LeadObrigadoView.as_view(), name="obrigado"),
    # Painel (exige sessão e permissão).
    path("painel/leads/", views.PainelLeadListView.as_view(), name="painel-lista"),
    path("painel/leads/<uuid:pk>/", views.PainelLeadDetailView.as_view(), name="painel-detalhe"),
    path(
        "painel/leads/<uuid:pk>/reclassificar/",
        views.PainelReclassificarView.as_view(),
        name="painel-reclassificar",
    ),
]
