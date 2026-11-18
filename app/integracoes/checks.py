"""Verificações do "manage.py check" para as integrações (sem expor valores)."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def rede_da_api_interna(app_configs, **kwargs):
    """Decisão P4: com o token definido e sem INTEGRACOES_N8N_REDES_PERMITIDAS, a API interna
    aceita chamadas de qualquer rede (só token + assinatura protegem)."""
    n8n = settings.INTEGRACOES["n8n"]
    if n8n["token"] and not n8n["redes_permitidas"]:
        return [
            Warning(
                "A API interna do n8n está sem restrição de rede.",
                hint="Defina INTEGRACOES_N8N_REDES_PERMITIDAS com o IP ou a rede (CIDR) do n8n.",
                id="integracoes.W001",
            )
        ]
    return []


@register(deploy=True)
def rede_atras_de_proxy(app_configs, **kwargs):
    """Com INTEGRACOES_N8N_REDES_PERMITIDAS definida e LEADS_PROXY_SALTOS=0, o IP considerado é o
    REMOTE_ADDR. Atrás de proxy reverso, esse é o IP do proxy: se ele estiver nas redes
    permitidas, qualquer origem passa na restrição e o bloqueio por falhas vira global."""
    if settings.INTEGRACOES["n8n"]["redes_permitidas"] and settings.LEADS_PROXY_SALTOS == 0:
        return [
            Warning(
                "A restrição de rede da API interna usa o IP da conexão (LEADS_PROXY_SALTOS=0).",
                hint=(
                    "Se houver proxy reverso na frente do Django, configure LEADS_PROXY_SALTOS e "
                    "LEADS_PROXY_CONFIAVEIS; se o n8n chama o Django direto pela rede interna, "
                    "silencie com SILENCED_SYSTEM_CHECKS."
                ),
                id="integracoes.W002",
            )
        ]
    return []
