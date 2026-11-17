"""Helpers e dados fictícios compartilhados pelos testes de leads."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.db import connection

from ..models import Lead
from ..services import registrar_lead


SQL_INSERIR = (
    "INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao) "
    "VALUES (%s, %s, %s, %s, %s, now(), '1.0') RETURNING id"
)


def inserir_lead_sql(nome="Ana Souza", email="ana@exemplo.com", telefone="+5511987654321",
                     empresa="Acme", cargo="CEO"):
    with connection.cursor() as cursor:
        cursor.execute(SQL_INSERIR, [nome, email, telefone, empresa, cargo])
        return Lead.objects.get(pk=cursor.fetchone()[0])


SENHA = "S3nha-forte-de-teste"


DADOS_LEAD = {
    "nome": "  Ana   Souza ",
    "email": "  Ana.Souza@Empresa.COM ",
    "telefone": "(11) 98765-4321",
    "empresa": "Acme",
    "cargo": "Diretora",
}


def criar_usuario(username="operador", **extra):
    dados = {"email": f"{username}@exemplo.com", "nome": "Operador Teste", "password": SENHA}
    dados.update(extra)
    return get_user_model().objects.create_user(username=username, **dados)


def criar_lead(**dados):
    lead_id, _ = registrar_lead(**{**DADOS_LEAD, **dados})
    return Lead.objects.get(pk=lead_id)


def permissao(app_label, codename):
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


def conceder(usuario, *perms):
    """perms no formato "app.codename"."""
    for perm in perms:
        app_label, codename = perm.split(".")
        usuario.user_permissions.add(permissao(app_label, codename))


FORM_VALIDO = {**DADOS_LEAD, "aceite_politica": "on"}


CONFIG_TESTE = {
    "POLITICA_PRIVACIDADE_VERSAO": "1.0",
    "POLITICA_PRIVACIDADE_URL": "",
    "LEADS_PUBLICO_TAXA": "1000/min",
    "LEADS_PROXY_SALTOS": 0,
    "LEADS_PROXY_CONFIAVEIS": [],
    "CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "leads-testes"}},
    # Sem manifest do collectstatic nos testes (mesmo padrão de app.users).
    "STORAGES": {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
}


URL_CAPTURA = "/api/leads/captura/"


CAPTURA_VALIDA = {**DADOS_LEAD, "aceite_politica": True}


class ComCacheLimpo:
    def setUp(self):
        super().setUp()
        cache.clear()


def basic(username):
    import base64
    return "Basic " + base64.b64encode(f"{username}:{SENHA}".encode()).decode()


XSS = "<script>alert(1)</script>"


XSS_ESCAPADO = "&lt;script&gt;alert(1)&lt;/script&gt;"
