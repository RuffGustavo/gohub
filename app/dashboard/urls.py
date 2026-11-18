from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    # O dashboard pessoal fica em users:painel (/painel/, destino do login).
    path("painel/gerencial/", views.PainelGerencialView.as_view(), name="gerencial"),
]
