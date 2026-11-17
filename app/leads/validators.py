"""Normalização e validação dos dados do lead (A1, A2).

Únicas implementações das regras, reutilizadas pelo model, pelos forms e pelos serializers.
O banco repete as mesmas regras (gatilho fn_lead_normalizar e CHECK ck_lead_*): aqui elas
existem para dar mensagens amigáveis antes de chegar ao banco.
"""

import re

import phonenumbers
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import EMAIL_REGEX, TELEFONE_E164_REGEX

MENSAGEM_EMAIL = "Informe um e-mail válido, por exemplo nome@empresa.com.br."
MENSAGEM_TELEFONE = "Informe um telefone válido com DDD, por exemplo (11) 98765-4321."
# Acima disso não é telefone: evita processar entradas gigantes.
TELEFONE_MAX_ENTRADA = 30


def normalizar_texto(valor):
    """Mesma regra do gatilho: espaços repetidos viram um só, sem espaços nas pontas."""
    return " ".join((valor or "").split())


def normalizar_obrigatorio(valor, *, minimo=1, mensagem):
    """Normaliza e exige ao menos `minimo` caracteres (texto só com espaços é vazio)."""
    valor = normalizar_texto(valor)
    if len(valor) < minimo:
        raise ValidationError(mensagem, code="required")
    return valor


# Padrões de dado pessoal que não devem ir para texto livre gravado em histórico (A11).
_PADROES_DADO_PESSOAL = [
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),                                  # e-mail
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),                           # CPF
    re.compile(r"(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?9?\d{4}[\s.-]?\d{4}\b"),  # telefone
]
MENSAGEM_DADO_PESSOAL = "Não inclua dados pessoais (e-mail, telefone ou CPF) na justificativa."


def normalizar_justificativa(valor):
    """Justificativa obrigatória e sem dados pessoais: ela fica no histórico imutável."""
    texto = normalizar_obrigatorio(valor, mensagem="Informe a justificativa.")
    if any(padrao.search(texto) for padrao in _PADROES_DADO_PESSOAL):
        raise ValidationError(MENSAGEM_DADO_PESSOAL, code="dado_pessoal")
    return texto


def normalizar_email(valor):
    """Trim + minúsculas (regra do gatilho) e formato válido (CHECK ck_lead_email)."""
    email = (valor or "").strip().lower()
    if not email:
        raise ValidationError("Informe o e-mail.", code="required")
    try:
        validate_email(email)
    except ValidationError:
        raise ValidationError(MENSAGEM_EMAIL, code="invalid") from None
    if len(email) > 254 or not re.match(EMAIL_REGEX, email):
        raise ValidationError(MENSAGEM_EMAIL, code="invalid")
    return email


def normalizar_telefone(valor, regiao_padrao="BR"):
    """Converte para E.164 (+5511987654321). Aceita máscara e número sem DDI (Brasil)."""
    texto = (valor or "").strip()
    if not texto:
        raise ValidationError("Informe o telefone.", code="required")
    if len(texto) > TELEFONE_MAX_ENTRADA:
        raise ValidationError(MENSAGEM_TELEFONE, code="invalid")
    try:
        numero = phonenumbers.parse(texto, regiao_padrao)
    except phonenumbers.NumberParseException:
        raise ValidationError(MENSAGEM_TELEFONE, code="invalid") from None
    if not phonenumbers.is_valid_number(numero):
        raise ValidationError(MENSAGEM_TELEFONE, code="invalid")
    e164 = phonenumbers.format_number(numero, phonenumbers.PhoneNumberFormat.E164)
    if not re.match(TELEFONE_E164_REGEX, e164):
        raise ValidationError(MENSAGEM_TELEFONE, code="invalid")
    return e164
