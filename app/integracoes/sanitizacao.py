"""Sanitização de texto livre recebido de sistemas externos (regra A11, I19).

Aplicada a mensagem_erro e observacao antes de gravar: dados pessoais detectáveis viram
marcadores e caracteres de controle/quebras viram espaço. Não há detecção perfeita de PII em
texto livre: isto reduz o risco, não o elimina (por isso o n8n deve enviar só códigos curtos).
"""

import re

TEXTO_MAX = 1000
_CONTROLE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
# 8 ou mais dígitos, com ou sem "+", espaços, "-", "." e parênteses entre eles (telefone, CPF...).
_TELEFONE = re.compile(r"\+?\(?\d(?:[\s().-]{0,2}\d){7,}\)?")


def sanitizar_texto(valor, limite=TEXTO_MAX):
    """Texto limpo (ou None se vazio), com no máximo `limite` caracteres."""
    if valor is None:
        return None
    texto = _CONTROLE.sub(" ", str(valor))
    texto = _EMAIL.sub("[email]", texto)
    texto = _TELEFONE.sub("[telefone]", texto)
    texto = " ".join(texto.split())
    return texto[:limite] or None
