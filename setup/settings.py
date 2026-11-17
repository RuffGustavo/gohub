from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured
import ipaddress
import os
import re

# Carrega o .env local (venv/bind mount). Não sobrescreve variáveis já definidas
# no ambiente, então valores do Docker Compose têm precedência.
load_dotenv()


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("A variável de ambiente SECRET_KEY é obrigatória.")

DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "drf_yasg",
    "app.users",
    "app.leads",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "setup.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "setup.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB"),
        "USER": os.getenv("POSTGRES_USER"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
        "HOST": os.getenv("POSTGRES_HOST"),
        "PORT": os.getenv("POSTGRES_PORT"),
    }
}

# Usuário interno da tabela "usuario" (MER). Definido antes do primeiro migrate.
AUTH_USER_MODEL = "users.Usuario"

# Área de acesso dos usuários (app.users). O Django Admin (/admin/) tem login próprio e só
# aceita quem tem is_staff (superusuário ou permissão "users.acessar_admin").
# Login do cliente na página inicial (/); depois dele, o dashboard.
LOGIN_URL = "users:login"
LOGIN_REDIRECT_URL = "users:painel"

# Django Admin (Jazzmin): identidade do GoHub/DashLite e caminho de volta ao painel (o Jazzmin
# não exibe o "Ver o site" do admin padrão). O tema visual fica em assets/css/gohub-admin.css;
# a fonte é a Roboto local (sem o CDN do Google, que o Jazzmin usa por padrão).
JAZZMIN_SETTINGS = {
    "site_title": "Administração | GoHub",
    "site_header": "GoHub",
    "site_brand": "GoHub",
    "site_logo": "images/favicon.png",
    "site_logo_classes": "",
    "login_logo": "images/favicon.png",
    "site_icon": "images/favicon.png",
    "welcome_sign": "Área administrativa do GoHub",
    "copyright": "GoHub",
    "custom_css": "assets/css/gohub-admin.css",
    "use_google_fonts_cdn": False,
    "topmenu_links": [
        {"name": "Voltar ao painel", "url": "users:painel"},
    ],
    "usermenu_links": [
        {"name": "Voltar ao painel", "url": "users:painel", "icon": "fas fa-arrow-left"},
    ],
}
JAZZMIN_UI_TWEAKS = {
    "navbar": "navbar-white navbar-light",
    "sidebar_nav_child_indent": True,
    "default_theme_mode": "light",
}

# Validade do link de redefinição de senha, em segundos (padrão do Django: 3 dias).
PASSWORD_RESET_TIMEOUT = int(os.getenv("PASSWORD_RESET_TIMEOUT", "3600"))

# E-mail (redefinição de senha). Com DEBUG, o padrão é o backend de console (o e-mail, com o
# link, aparece no terminal); sem DEBUG, SMTP, configurado pelas variáveis EMAIL_*.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND") or (
    "django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "GoHub <nao-responda@localhost>")

# Argon2id como padrão (MER, regras A5/B14; argon2-cffi já está em requirements.txt).
# PBKDF2 permanece para verificar hashes nesse formato.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "pt-BR"

TIME_ZONE = "America/Sao_Paulo"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "setup/static"),
]

STATIC_ROOT = os.path.join(BASE_DIR, "static")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "setup.storage.ManifestStaticFilesStorage"},
}

MEDIA_ROOT = os.path.join(BASE_DIR, "media")

MEDIA_URL = "/media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.DjangoModelPermissions",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# Logs para stdout/stderr (coletados pelo Docker).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
    # Remove o handler padrão do logger "django" para não duplicar mensagens no root.
    "loggers": {"django": {"handlers": [], "propagate": True}},
}

# Celery (broker Redis). A URL é montada a partir de REDIS_*, no mesmo padrão de POSTGRES_*.
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = os.getenv("REDIS_DB", "0")
_redis_auth = f":{quote(REDIS_PASSWORD, safe='')}@" if REDIS_PASSWORD else ""

CELERY_BROKER_URL = f"redis://{_redis_auth}{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = TIME_ZONE

# Cache compartilhado entre os processos (mesmo Redis do Celery, em outro banco lógico).
# Hoje só guarda os contadores do rate limit da captação pública (app.leads.throttles).
REDIS_CACHE_DB = os.getenv("REDIS_CACHE_DB", "1")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"redis://{_redis_auth}{REDIS_HOST}:{REDIS_PORT}/{REDIS_CACHE_DB}",
        "KEY_PREFIX": "gohub",
    }
}

# Leads (app.leads): consentimento LGPD e proteção da captação pública.
# Versão da política aceita no formulário (gravada em lead.politica_versao, até 20 caracteres).
POLITICA_PRIVACIDADE_VERSAO = os.getenv("POLITICA_PRIVACIDADE_VERSAO", "1.0").strip()
if not 1 <= len(POLITICA_PRIVACIDADE_VERSAO) <= 20:
    raise ImproperlyConfigured("POLITICA_PRIVACIDADE_VERSAO deve ter de 1 a 20 caracteres.")
# Link exibido no formulário público; vazio oculta o link.
POLITICA_PRIVACIDADE_URL = os.getenv("POLITICA_PRIVACIDADE_URL", "").strip()
if POLITICA_PRIVACIDADE_URL and (
    not POLITICA_PRIVACIDADE_URL.startswith(("https://", "/")) or POLITICA_PRIVACIDADE_URL.startswith("//")
):
    raise ImproperlyConfigured("POLITICA_PRIVACIDADE_URL deve começar com https:// ou / (caminho do próprio site).")
# Envios por IP na tela e na API de captação, somados (formato do DRF: 10/min, 100/hour...).
LEADS_PUBLICO_TAXA = os.getenv("LEADS_PUBLICO_TAXA", "10/min").strip()
if not re.fullmatch(r"[1-9][0-9]*/(s|sec|m|min|h|hour|d|day)", LEADS_PUBLICO_TAXA):
    raise ImproperlyConfigured("LEADS_PUBLICO_TAXA deve estar no formato <número>/<s|min|hour|day>, ex.: 10/min.")
# Quantos proxies confiáveis ficam na frente do Django. 0 = usa o IP da conexão (REMOTE_ADDR)
# e ignora X-Forwarded-For (que o cliente consegue forjar).
LEADS_PROXY_SALTOS = int(os.getenv("LEADS_PROXY_SALTOS", "0"))
if LEADS_PROXY_SALTOS < 0:
    raise ImproperlyConfigured("LEADS_PROXY_SALTOS não pode ser negativo.")
# IPs/redes (CIDR) dos proxies confiáveis: o X-Forwarded-For só é lido se a conexão vier de um
# deles (outro container da rede não consegue forjar o IP do visitante).
LEADS_PROXY_CONFIAVEIS = env_list("LEADS_PROXY_CONFIAVEIS")
try:
    for _rede in LEADS_PROXY_CONFIAVEIS:
        ipaddress.ip_network(_rede, strict=False)
except ValueError:
    raise ImproperlyConfigured("LEADS_PROXY_CONFIAVEIS deve listar IPs ou redes CIDR separados por vírgula.") from None
if LEADS_PROXY_SALTOS > 0 and not LEADS_PROXY_CONFIAVEIS:
    raise ImproperlyConfigured("Com LEADS_PROXY_SALTOS > 0, informe os proxies em LEADS_PROXY_CONFIAVEIS.")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"
MINIO_REGION = os.getenv("MINIO_REGION")
