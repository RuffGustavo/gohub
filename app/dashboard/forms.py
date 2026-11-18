from django import forms
from django.core.exceptions import ValidationError

from app.users.models import CanalAcesso, StatusAcesso

BUSCA_USUARIO_MIN = 2
BUSCA_USUARIO_MAX = 50


class FiltroHistoricoForm(forms.Form):
    """Filtro extra do histórico pessoal (o período vem de FiltroPeriodoForm)."""

    status = forms.ChoiceField(label="Status", choices=[("", "Todos")] + StatusAcesso.choices, required=False)

    def filtros(self):
        self.is_valid()
        return {"status": self.cleaned_data.get("status") or None}


class FiltroAcessoForm(forms.Form):
    """Filtros do histórico geral de acessos (painel gerencial), usado com prefix="acesso"."""

    status = forms.ChoiceField(label="Status", choices=[("", "Todos")] + StatusAcesso.choices, required=False)
    canal = forms.ChoiceField(label="Canal", choices=[("", "Todos")] + CanalAcesso.choices, required=False)
    usuario = forms.CharField(
        label="Usuário",
        required=False,
        max_length=BUSCA_USUARIO_MAX,
        help_text=f"Parte do nome de usuário (de {BUSCA_USUARIO_MIN} a {BUSCA_USUARIO_MAX} caracteres).",
    )

    def clean_usuario(self):
        termo = self.cleaned_data["usuario"].strip().lower()
        if termo and len(termo) < BUSCA_USUARIO_MIN:
            raise ValidationError(f"Digite ao menos {BUSCA_USUARIO_MIN} caracteres para buscar.")
        return termo

    def filtros(self):
        """Só os campos válidos (inválido não filtra, como em FiltroLeadForm)."""
        self.is_valid()
        dados = self.cleaned_data
        return {campo: dados.get(campo) or None for campo in ("status", "canal", "usuario")}
