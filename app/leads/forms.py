from django import forms
from django.core.exceptions import ValidationError

from .models import ClassificacaoLead, LeadStatus
from .selectors import SEM_CLASSIFICACAO
from .validators import (
    TELEFONE_MAX_ENTRADA,
    normalizar_email,
    normalizar_justificativa,
    normalizar_obrigatorio,
    normalizar_telefone,
)

BUSCA_MIN = 2
BUSCA_MAX = 100
JUSTIFICATIVA_MAX = 2000


class LeadPublicoForm(forms.Form):
    """Formulário público de captação (A1). Os limites são os do model; a normalização é a
    mesma do gatilho fn_lead_normalizar (app/leads/validators.py)."""

    nome = forms.CharField(label="Nome", max_length=150)
    email = forms.CharField(label="E-mail", max_length=254)
    telefone = forms.CharField(
        label="Telefone", max_length=TELEFONE_MAX_ENTRADA, help_text="Com DDD, por exemplo (11) 98765-4321."
    )
    empresa = forms.CharField(label="Empresa", max_length=150)
    cargo = forms.CharField(label="Cargo", max_length=100)
    aceite_politica = forms.BooleanField(
        label="Li e aceito a política de privacidade",
        error_messages={"required": "É necessário aceitar a política de privacidade para enviar."},
    )
    # Honeypot antispam (A4): invisível para pessoas; robôs costumam preenchê-lo.
    website = forms.CharField(label="Não preencha este campo", required=False, max_length=200)

    def clean_nome(self):
        return normalizar_obrigatorio(
            self.cleaned_data["nome"], minimo=2, mensagem="Informe seu nome (ao menos 2 letras)."
        )

    def clean_email(self):
        return normalizar_email(self.cleaned_data["email"])

    def clean_telefone(self):
        return normalizar_telefone(self.cleaned_data["telefone"])

    def clean_empresa(self):
        return normalizar_obrigatorio(self.cleaned_data["empresa"], mensagem="Informe a empresa.")

    def clean_cargo(self):
        return normalizar_obrigatorio(self.cleaned_data["cargo"], mensagem="Informe o cargo.")

    @property
    def is_bot(self):
        """Honeypot preenchido (olha o dado bruto: vale mesmo com o form inválido)."""
        return bool((self.data.get(self.add_prefix("website")) or "").strip())


class FiltroLeadForm(forms.Form):
    """Filtros do painel e da API (GET). Valores inválidos viram erro do campo e não filtram."""

    status = forms.ChoiceField(label="Status", choices=[("", "Todos")] + LeadStatus.choices, required=False)
    classificacao = forms.ChoiceField(
        label="Classificação",
        choices=[("", "Todas"), (SEM_CLASSIFICACAO, "Não classificado")] + ClassificacaoLead.choices,
        required=False,
    )
    inicio = forms.DateField(label="Recebido de", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fim = forms.DateField(label="Recebido até", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    q = forms.CharField(
        label="Busca",
        required=False,
        max_length=BUSCA_MAX,
        help_text=f"Nome, e-mail ou empresa (de {BUSCA_MIN} a {BUSCA_MAX} caracteres).",
    )

    def clean_q(self):
        termo = " ".join(self.cleaned_data["q"].split())
        if termo and len(termo) < BUSCA_MIN:
            raise ValidationError(f"Digite ao menos {BUSCA_MIN} caracteres para buscar.")
        return termo

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get("inicio"), dados.get("fim")
        if inicio and fim and fim < inicio:
            self.add_error("fim", "A data final deve ser igual ou posterior à inicial.")
        return dados

    def filtros(self):
        """Argumentos de selectors.filtrar_leads só com os campos válidos."""
        self.is_valid()
        dados = self.cleaned_data
        return {
            "status": dados.get("status") or None,
            "classificacao": dados.get("classificacao") or None,
            "inicio": dados.get("inicio"),
            "fim": dados.get("fim"),
            "busca": dados.get("q") or None,
        }


class ReclassificacaoForm(forms.Form):
    classificacao = forms.ChoiceField(label="Nova classificação", choices=ClassificacaoLead.choices)
    justificativa = forms.CharField(
        label="Justificativa",
        max_length=JUSTIFICATIVA_MAX,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Motivo da mudança. Não inclua dados pessoais (e-mail, telefone, documentos).",
    )

    def clean_justificativa(self):
        return normalizar_justificativa(self.cleaned_data["justificativa"])
