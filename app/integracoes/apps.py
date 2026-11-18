from django.apps import AppConfig


class IntegracoesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.integracoes"
    label = "integracoes"
    verbose_name = "Integrações"

    def ready(self):
        from . import checks  # noqa: F401  (registra as verificações do manage.py check)
