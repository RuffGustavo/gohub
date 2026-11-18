from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """Dashboards (pessoal e gerencial): só leitura e agregação, sem models próprios.

    Os dados vêm de users (Usuario, RegistroAcesso) e leads (Lead, LeadStatusHistorico).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "app.dashboard"
    label = "dashboard"
    verbose_name = "Dashboards"
