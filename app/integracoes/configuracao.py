"""Leitura e validação das variáveis INTEGRACOES_* (chamado por setup/settings.py).

Função pura sobre um dicionário de ambiente (os.environ): não importa models nem settings,
para poder ser usada na carga do settings e testada isoladamente. Erros levantam
ImproperlyConfigured citando a VARIÁVEL, nunca o valor.

Credenciais (decisão P5) só existem em variáveis de ambiente; nunca em tabela, tela ou log.
Rotação: ver o docstring de app/integracoes/authentication.py.
"""

import ipaddress
import re
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

SEGREDO_MINIMO = 32
TIMEOUT_TOTAL_MAX = 25
TAXA_REGEX = r"[1-9][0-9]*/(s|sec|m|min|h|hour|d|day)"


def _texto(env, nome):
    return (env.get(nome) or "").strip()


def _booleano(env, nome):
    return _texto(env, nome).lower() in ("1", "true", "yes", "on")


def _segredo(env, nome):
    valor = _texto(env, nome)
    if valor and len(valor) < SEGREDO_MINIMO:
        raise ImproperlyConfigured(f"{nome} deve ter ao menos {SEGREDO_MINIMO} caracteres.")
    return valor


def _inteiro(env, nome, padrao, minimo, maximo):
    bruto = _texto(env, nome)
    try:
        valor = int(bruto) if bruto else padrao
    except ValueError:
        raise ImproperlyConfigured(f"{nome} deve ser um número inteiro.") from None
    if not minimo <= valor <= maximo:
        raise ImproperlyConfigured(f"{nome} deve estar entre {minimo} e {maximo}.")
    return valor


def _taxa(env, nome, padrao):
    valor = _texto(env, nome) or padrao
    if not re.fullmatch(TAXA_REGEX, valor):
        raise ImproperlyConfigured(f"{nome} deve estar no formato <número>/<s|min|hour|day>, ex.: 600/min.")
    return valor


def _redes(env, nome):
    redes = [item.strip() for item in _texto(env, nome).split(",") if item.strip()]
    try:
        return [str(ipaddress.ip_network(rede, strict=False)) for rede in redes]
    except ValueError:
        raise ImproperlyConfigured(f"{nome} deve listar IPs ou redes CIDR separados por vírgula.") from None


def _url_webhook(env, nome, permitir_http):
    """https obrigatório (decisão P6); http só com permissão explícita; sem credenciais na URL."""
    url = _texto(env, nome)
    if not url:
        return ""
    try:
        partes = urlsplit(url)
        partes.port  # porta inválida levanta ValueError
    except ValueError:
        raise ImproperlyConfigured(f"{nome} não é uma URL válida.") from None
    esquemas = ("https", "http") if permitir_http else ("https",)
    if partes.scheme not in esquemas:
        raise ImproperlyConfigured(
            f"{nome} deve usar https:// (http:// só com INTEGRACOES_N8N_WEBHOOK_PERMITIR_HTTP=true)."
        )
    if not partes.hostname:
        raise ImproperlyConfigured(f"{nome} não é uma URL válida.")
    if partes.username is not None or partes.password is not None:
        raise ImproperlyConfigured(f"{nome} não pode conter usuário ou senha.")
    if partes.fragment:
        raise ImproperlyConfigured(f"{nome} não pode conter fragmento (#).")
    return url


def carregar(env):
    """Monta settings.INTEGRACOES a partir do ambiente."""
    token = _segredo(env, "INTEGRACOES_N8N_TOKEN")
    token_anterior = _segredo(env, "INTEGRACOES_N8N_TOKEN_ANTERIOR")
    segredo = _segredo(env, "INTEGRACOES_N8N_SEGREDO_HMAC")
    segredo_anterior = _segredo(env, "INTEGRACOES_N8N_SEGREDO_HMAC_ANTERIOR")
    permitir_http = _booleano(env, "INTEGRACOES_N8N_WEBHOOK_PERMITIR_HTTP")
    url = _url_webhook(env, "INTEGRACOES_N8N_WEBHOOK_URL", permitir_http)

    if token_anterior and not token:
        raise ImproperlyConfigured("INTEGRACOES_N8N_TOKEN_ANTERIOR exige INTEGRACOES_N8N_TOKEN.")
    if token_anterior and token_anterior == token:
        raise ImproperlyConfigured("INTEGRACOES_N8N_TOKEN_ANTERIOR deve ser diferente de INTEGRACOES_N8N_TOKEN.")
    if segredo_anterior and not segredo:
        raise ImproperlyConfigured("INTEGRACOES_N8N_SEGREDO_HMAC_ANTERIOR exige INTEGRACOES_N8N_SEGREDO_HMAC.")
    if segredo_anterior and segredo_anterior == segredo:
        raise ImproperlyConfigured(
            "INTEGRACOES_N8N_SEGREDO_HMAC_ANTERIOR deve ser diferente de INTEGRACOES_N8N_SEGREDO_HMAC."
        )
    # A saída é assinada e a entrada exige assinatura (decisão P3): sem segredo, nenhuma das duas funciona.
    if url and not segredo:
        raise ImproperlyConfigured("INTEGRACOES_N8N_WEBHOOK_URL exige INTEGRACOES_N8N_SEGREDO_HMAC.")
    if token and not segredo:
        raise ImproperlyConfigured("INTEGRACOES_N8N_TOKEN exige INTEGRACOES_N8N_SEGREDO_HMAC.")

    timeout_conexao = _inteiro(env, "INTEGRACOES_TIMEOUT_CONEXAO", 3, 1, 30)
    timeout_leitura = _inteiro(env, "INTEGRACOES_TIMEOUT_LEITURA", 5, 1, 30)
    # A task de notificação tem time_limit de 30 s: o envio precisa caber com folga.
    if timeout_conexao + timeout_leitura > TIMEOUT_TOTAL_MAX:
        raise ImproperlyConfigured(
            f"INTEGRACOES_TIMEOUT_CONEXAO + INTEGRACOES_TIMEOUT_LEITURA deve ser no máximo {TIMEOUT_TOTAL_MAX}."
        )

    return {
        "n8n": {
            "token": token,
            "token_anterior": token_anterior,
            "segredo_hmac": segredo,
            "segredo_hmac_anterior": segredo_anterior,
            "webhook_url": url,
            "redes_permitidas": _redes(env, "INTEGRACOES_N8N_REDES_PERMITIDAS"),
        },
        "timeout_conexao": timeout_conexao,
        "timeout_leitura": timeout_leitura,
        "max_tentativas": _inteiro(env, "INTEGRACOES_MAX_TENTATIVAS", 5, 1, 20),
        "interno_taxa": _taxa(env, "INTEGRACOES_INTERNO_TAXA", "600/min"),
        "auth_falha_taxa": _taxa(env, "INTEGRACOES_AUTH_FALHA_TAXA", "20/min"),
    }
