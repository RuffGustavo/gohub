"""Resumo do User-Agent para o registro de acessos (sem dependência externa).

Guarda só navegador, sistema e tipo de dispositivo: o User-Agent completo identifica
demais o aparelho (minimização, LGPD). É uma heurística: o cabeçalho é informado pelo
cliente e pode ser falsificado; serve para o titular reconhecer os próprios acessos,
não como prova de autoria.
"""

import re

# Ordem importa: Edge e Opera também anunciam "Chrome"; Chrome também anuncia "Safari".
_NAVEGADORES = [
    ("Edge", re.compile(r"Edg(e|A|iOS)?/")),
    ("Opera", re.compile(r"(OPR|Opera)/")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/")),
    ("Firefox", re.compile(r"(Firefox|FxiOS)/")),
    ("Chrome", re.compile(r"(Chrome|CriOS)/")),
    ("Safari", re.compile(r"Version/[\d.]+.*Safari/")),
]
_SISTEMAS = [
    ("Windows", re.compile(r"Windows")),
    ("Android", re.compile(r"Android")),
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("ChromeOS", re.compile(r"CrOS")),
    ("Linux", re.compile(r"Linux")),
]
_TABLET = re.compile(r"iPad|Tablet|PlayBook|Silk/|(Android(?!.*Mobile))")
_CELULAR = re.compile(r"Mobi|iPhone|iPod|Android.*Mobile|Windows Phone")
_NAVEGADOR_WEB = re.compile(r"^Mozilla/")

# Lido no máximo até aqui: o cabeçalho vem do cliente e não tem tamanho limitado.
TAMANHO_MAXIMO = 512


def _primeiro(regras, texto):
    return next((nome for nome, padrao in regras if padrao.search(texto)), "")


def resumir_user_agent(user_agent):
    """"Mozilla/5.0 (iPhone ...) ... Safari/604.1" -> ("Safari", "iOS", "celular").

    Retorna (navegador, sistema_operacional, dispositivo); valores vazios / "outro" quando
    não reconhecidos (ex.: scripts, curl, clientes de API).
    """
    texto = (user_agent or "")[:TAMANHO_MAXIMO]
    if not _NAVEGADOR_WEB.match(texto):
        return "", "", "outro"
    navegador = _primeiro(_NAVEGADORES, texto)
    sistema = _primeiro(_SISTEMAS, texto)
    if _TABLET.search(texto):
        dispositivo = "tablet"
    elif _CELULAR.search(texto):
        dispositivo = "celular"
    elif sistema in {"Windows", "macOS", "Linux", "ChromeOS"}:
        dispositivo = "computador"
    else:
        dispositivo = "outro"
    return navegador, sistema, dispositivo
