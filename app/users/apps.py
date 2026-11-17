from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.users"
    label = "users"
    verbose_name = "Usuários"

    def ready(self):
        from . import auditoria  # noqa: F401  (conecta os receptores dos sinais de autenticação)
