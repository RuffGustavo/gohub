from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .forms import JUSTIFICATIVA_MAX
from .models import ClassificacaoLead, Lead, LeadClassificacao, LeadStatusHistorico
from .validators import (
    TELEFONE_MAX_ENTRADA,
    normalizar_email,
    normalizar_justificativa,
    normalizar_obrigatorio,
    normalizar_telefone,
)


def _validar(funcao, *args, **kwargs):
    """Reaproveita os normalizadores (ValidationError do Django -> do DRF)."""
    try:
        return funcao(*args, **kwargs)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.messages) from None


class LeadCapturaSerializer(serializers.Serializer):
    """Entrada da captação pública. Campos fora desta lista (ex.: status) são ignorados."""

    nome = serializers.CharField(max_length=150, trim_whitespace=False)
    email = serializers.CharField(max_length=254, trim_whitespace=False)
    telefone = serializers.CharField(max_length=TELEFONE_MAX_ENTRADA, trim_whitespace=False)
    empresa = serializers.CharField(max_length=150, trim_whitespace=False)
    cargo = serializers.CharField(max_length=100, trim_whitespace=False)
    aceite_politica = serializers.BooleanField()
    website = serializers.CharField(max_length=200, required=False, allow_blank=True, write_only=True)

    def validate_nome(self, valor):
        return _validar(normalizar_obrigatorio, valor, minimo=2, mensagem="Informe seu nome (ao menos 2 letras).")

    def validate_email(self, valor):
        return _validar(normalizar_email, valor)

    def validate_telefone(self, valor):
        return _validar(normalizar_telefone, valor)

    def validate_empresa(self, valor):
        return _validar(normalizar_obrigatorio, valor, mensagem="Informe a empresa.")

    def validate_cargo(self, valor):
        return _validar(normalizar_obrigatorio, valor, mensagem="Informe o cargo.")

    def validate_aceite_politica(self, valor):
        if valor is not True:
            raise serializers.ValidationError("É necessário aceitar a política de privacidade para enviar.")
        return valor


class LeadListSerializer(serializers.ModelSerializer):
    recebido_em = serializers.DateTimeField(source="created_at", read_only=True)
    status_display = serializers.CharField(source="get_status_atual_display", read_only=True)
    classificacao_display = serializers.CharField(source="get_classificacao_atual_display", read_only=True)

    class Meta:
        model = Lead
        fields = [
            "id", "nome", "email", "telefone", "empresa", "cargo", "recebido_em",
            "classificacao_atual", "classificacao_display", "status_atual", "status_display",
        ]
        read_only_fields = fields


class LeadDetailSerializer(LeadListSerializer):
    class Meta(LeadListSerializer.Meta):
        fields = LeadListSerializer.Meta.fields + [
            "status_atualizado_em", "classificado_em", "consentimento_em", "politica_versao", "updated_at",
        ]
        read_only_fields = fields


class LeadStatusHistoricoSerializer(serializers.ModelSerializer):
    autor = serializers.CharField(read_only=True)
    status_anterior_display = serializers.CharField(source="get_status_anterior_display", read_only=True)
    status_novo_display = serializers.CharField(source="get_status_novo_display", read_only=True)

    class Meta:
        model = LeadStatusHistorico
        fields = [
            "id", "status_anterior", "status_anterior_display", "status_novo", "status_novo_display",
            "autor", "observacao", "ocorrido_em",
        ]
        read_only_fields = fields


class LeadClassificacaoSerializer(serializers.ModelSerializer):
    autor = serializers.CharField(read_only=True)
    classificacao_display = serializers.CharField(source="get_classificacao_display", read_only=True)
    metodo_display = serializers.CharField(source="get_metodo_display", read_only=True)

    class Meta:
        model = LeadClassificacao
        fields = [
            "id", "classificacao", "classificacao_display", "metodo", "metodo_display",
            "justificativa", "versao_criterio", "confianca", "autor", "classificado_em",
        ]
        read_only_fields = fields


class ReclassificacaoSerializer(serializers.Serializer):
    """Entrada da reclassificação manual: método e autor são definidos pelo servidor."""

    classificacao = serializers.ChoiceField(choices=ClassificacaoLead.choices)
    justificativa = serializers.CharField(max_length=JUSTIFICATIVA_MAX, trim_whitespace=False)

    def validate_justificativa(self, valor):
        return _validar(normalizar_justificativa, valor)
