"""Assinatura HMAC-SHA256 dos webhooks de saída e das chamadas de entrada (decisão P3).

- Saída (aplicação -> n8n): "{timestamp}.{corpo bruto}".
- Entrada (n8n -> aplicação): "{timestamp}.{MÉTODO}.{caminho com query}.{corpo bruto}" (GET: corpo
  vazio). Método e caminho na mensagem impedem reaproveitar a assinatura em outra rota ou lead.
Timestamp Unix em segundos; cabeçalho "sha256=<64 hex minúsculos>"; comparação em tempo constante.
"""

import hashlib
import hmac
import re
import time

PREFIXO = "sha256="
JANELA_SEGUNDOS = 300
_ASSINATURA_REGEX = re.compile(r"sha256=[0-9a-f]{64}")
_TIMESTAMP_REGEX = re.compile(r"[0-9]{1,12}")


def assinar(corpo: bytes, timestamp, segredo: str) -> str:
    mensagem = f"{timestamp}.".encode() + corpo
    return PREFIXO + hmac.new(segredo.encode(), mensagem, hashlib.sha256).hexdigest()


def mensagem_entrada(metodo, caminho, corpo: bytes) -> bytes:
    """Parte da mensagem de entrada depois do timestamp: "MÉTODO.caminho.corpo"."""
    return f"{metodo.upper()}.{caminho}.".encode() + corpo


def assinatura_valida(corpo: bytes, timestamp, assinatura, segredos) -> bool:
    """True se `assinatura` confere com algum dos segredos (atual ou anterior).

    Compara com todos os segredos configurados, sem retorno antecipado.
    """
    if not isinstance(assinatura, str) or not _ASSINATURA_REGEX.fullmatch(assinatura):
        return False
    valida = False
    for segredo in segredos:
        if segredo:
            valida |= hmac.compare_digest(assinar(corpo, timestamp, segredo), assinatura)
    return valida


def timestamp_valido(valor, agora=None):
    """Inteiro Unix dentro de ±JANELA_SEGUNDOS do relógio local; senão None."""
    if not isinstance(valor, str) or not _TIMESTAMP_REGEX.fullmatch(valor):
        return None
    timestamp = int(valor)
    agora = time.time() if agora is None else agora
    if abs(agora - timestamp) > JANELA_SEGUNDOS:
        return None
    return timestamp
