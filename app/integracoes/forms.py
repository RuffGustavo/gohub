"""Filtros (GET) do painel e da API de integrações. Valores fora do domínio viram erro do
campo: a tela ignora o filtro inválido e a API responde 400."""

from django import forms

from app.leads.models import Sistema

from .models import TIPOS_EVENTO, ResultadoEvento, StatusIntegracao
from .selectors import STATUS_FILA_PADRAO

SISTEMAS = [
    (Sistema.N8N, "n8n"),
    (Sistema.CRM, "CRM"),
    (Sistema.NOTIFICACAO, "Notificação"),
    (Sistema.APLICACAO_WEB, "Aplicação web"),
]


class _FiltroPeriodo(forms.Form):
    lead = forms.UUIDField(label="Lead (ID)", required=False)
    inicio = forms.DateField(label="De", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fim = forms.DateField(label="Até", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get("inicio"), dados.get("fim")
        if inicio and fim and fim < inicio:
            self.add_error("fim", "A data final deve ser igual ou posterior à inicial.")
        return dados

    def _validos(self):
        self.is_valid()
        return self.cleaned_data


class FiltroIntegracaoForm(_FiltroPeriodo):
    status = forms.MultipleChoiceField(
        label="Status", choices=StatusIntegracao.choices, required=False, widget=forms.CheckboxSelectMultiple
    )
    sistema = forms.ChoiceField(label="Sistema", choices=[("", "Todos")] + SISTEMAS, required=False)

    def __init__(self, *args, status_padrao=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.status_padrao = list(status_padrao)

    def filtros(self):
        dados = self._validos()
        status = dados.get("status") or []
        if not status and "status" not in self.data:
            status = self.status_padrao
        return {
            "status": status,
            "sistema": dados.get("sistema") or None,
            "lead": dados.get("lead"),
            "inicio": dados.get("inicio"),
            "fim": dados.get("fim"),
        }


class FiltroFilaForm(FiltroIntegracaoForm):
    """Tela da fila: sem status escolhido, mostra pendente + erro."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, status_padrao=STATUS_FILA_PADRAO, **kwargs)
        if not self.is_bound or "status" not in self.data:
            self.initial["status"] = list(STATUS_FILA_PADRAO)


class FiltroEventoForm(_FiltroPeriodo):
    sistema = forms.ChoiceField(label="Sistema", choices=[("", "Todos")] + SISTEMAS, required=False)
    resultado = forms.ChoiceField(label="Resultado", choices=[("", "Todos")] + ResultadoEvento.choices, required=False)
    tipo_evento = forms.ChoiceField(
        label="Tipo", choices=[("", "Todos")] + [(tipo, tipo) for tipo in TIPOS_EVENTO], required=False
    )

    def filtros(self):
        dados = self._validos()
        return {
            "sistema": dados.get("sistema") or None,
            "resultado": dados.get("resultado") or None,
            "tipo_evento": dados.get("tipo_evento") or None,
            "lead": dados.get("lead"),
            "inicio": dados.get("inicio"),
            "fim": dados.get("fim"),
        }

