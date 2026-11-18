"""Serializers das integrações: entrada da API interna (n8n) e saída das APIs (somente leitura).

Entrada: campos desconhecidos -> 400 (SemCamposDesconhecidos); texto livre sanitizado
(sanitizacao.py) e justificativa sem dados pessoais (app.leads.validators).
Saída: fields explícitos, tudo read_only; payload e mensagem_erro só no detalhe do evento.
"""

import datetime
import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from app.leads.models import ClassificacaoLead, Lead, LeadStatus, MetodoClassificacao, Sistema
from app.leads.validators import normalizar_justificativa

from .models import (
    CHAVE_IDEMPOTENCIA_REGEX,
    ID_EXTERNO_REGEX,
    PAYLOAD_BYTES_MAX,
    PAYLOAD_CHAVES,
    PAYLOAD_TEXTO_MAX,
    TIPOS_EVENTO_ENTRADA,
    EventoIntegracao,
    LeadIntegracao,
    ResultadoEvento,
)
from .sanitizacao import TEXTO_MAX, sanitizar_texto

FUTURO_TOLERADO = datetime.timedelta(minutes=5)
METODOS_SISTEMA = [(valor, rotulo) for valor, rotulo in MetodoClassificacao.choices if valor != MetodoClassificacao.MANUAL]
STATUS_ENTRADA = [(valor, rotulo) for valor, rotulo in LeadStatus.choices if valor != LeadStatus.RECEBIDO]


class SemCamposDesconhecidos:
    """Recusa (400) qualquer chave fora de `fields` em vez de ignorá-la em silêncio."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            desconhecidos = sorted(set(data) - set(self.fields))
            if desconhecidos:
                raise serializers.ValidationError({campo: ["Campo não permitido."] for campo in desconhecidos[:20]})
        return super().to_internal_value(data)


class DataHoraComFuso(serializers.DateTimeField):
    """ISO 8601 obrigatoriamente com fuso (sem fuso não se sabe o instante na origem)."""

    def to_internal_value(self, valor):
        if not isinstance(valor, str) or len(valor) > 40:
            self.fail("invalid", format="ISO 8601 com fuso")
        convertido = parse_datetime(valor)
        if convertido is None or convertido.tzinfo is None:
            raise serializers.ValidationError("Informe data e hora ISO 8601 com fuso, ex.: 2026-01-31T14:05:00-03:00.")
        return super().to_internal_value(valor)


# --------------------------------------------------------------------------- entrada
class ClassificacaoEntradaSerializer(SemCamposDesconhecidos, serializers.Serializer):
    classificacao = serializers.ChoiceField(choices=ClassificacaoLead.choices)
    metodo = serializers.ChoiceField(choices=METODOS_SISTEMA)
    justificativa = serializers.CharField(max_length=2000, required=False, allow_null=True, trim_whitespace=False)
    versao_criterio = serializers.CharField(max_length=50, required=False, allow_null=True)
    confianca = serializers.DecimalField(
        max_digits=5, decimal_places=4, min_value=0, max_value=1, required=False, allow_null=True
    )

    def validate_justificativa(self, valor):
        if valor is None or not valor.strip():
            return None
        try:
            return normalizar_justificativa(valor)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from None

    def validate_versao_criterio(self, valor):
        return sanitizar_texto(valor, limite=50)

    def validate(self, dados):
        if dados.get("confianca") is not None and dados["metodo"] != MetodoClassificacao.IA:
            raise serializers.ValidationError({"confianca": "Confiança só se aplica a classificações por IA."})
        return dados


class EventoEntradaSerializer(SemCamposDesconhecidos, serializers.Serializer):
    """Corpo do POST /api/interno/leads/<id>/eventos/. Contexto: `sistema` (chamador) e `lead`."""

    chave_idempotencia = serializers.RegexField(CHAVE_IDEMPOTENCIA_REGEX, max_length=100)
    tipo_evento = serializers.ChoiceField(choices=TIPOS_EVENTO_ENTRADA)
    ocorrido_em = DataHoraComFuso()
    resultado = serializers.ChoiceField(choices=ResultadoEvento.choices)
    sistema_destino = serializers.CharField(max_length=30, required=False, allow_null=True)
    id_externo = serializers.RegexField(ID_EXTERNO_REGEX, max_length=100, required=False, allow_null=True)
    mensagem_erro = serializers.CharField(max_length=TEXTO_MAX, required=False, allow_null=True, trim_whitespace=False)
    payload = serializers.JSONField(required=False, allow_null=True)
    classificacao = ClassificacaoEntradaSerializer(required=False, allow_null=True)
    status_novo = serializers.ChoiceField(choices=STATUS_ENTRADA, required=False, allow_null=True)
    observacao = serializers.CharField(max_length=TEXTO_MAX, required=False, allow_null=True, trim_whitespace=False)

    def validate_ocorrido_em(self, valor):
        if valor > timezone.now() + FUTURO_TOLERADO:
            raise serializers.ValidationError("A data não pode estar mais de 5 minutos no futuro.")
        lead = self.context["lead"]
        if valor < lead.created_at:
            raise serializers.ValidationError("A data não pode ser anterior ao recebimento do lead.")
        return valor

    def validate_sistema_destino(self, valor):
        if valor is None:
            return None
        chamador = self.context["sistema"]
        sistema = Sistema.objects.filter(codigo=valor, ativo=True).first()
        if sistema is None or sistema.codigo == Sistema.APLICACAO_WEB or sistema.pk == chamador.pk:
            raise serializers.ValidationError("Sistema de destino inválido.")
        return sistema

    def validate_mensagem_erro(self, valor):
        return sanitizar_texto(valor)

    def validate_observacao(self, valor):
        return sanitizar_texto(valor)

    def validate_payload(self, valor):
        """Decisão P10: lista fechada de chaves escalares, até 4 KB (sem PII nem credenciais)."""
        if valor is None:
            return None
        if not isinstance(valor, dict):
            raise serializers.ValidationError("O payload deve ser um objeto JSON.")
        proibidas = sorted(set(valor) - set(PAYLOAD_CHAVES))
        if proibidas:
            raise serializers.ValidationError(f"Chaves não permitidas no payload: {', '.join(proibidas[:10])[:200]}.")
        for chave, item in valor.items():
            if isinstance(item, str):
                if len(item) > PAYLOAD_TEXTO_MAX:
                    raise serializers.ValidationError(f"O valor de {chave} passa de {PAYLOAD_TEXTO_MAX} caracteres.")
            elif item is not None and not isinstance(item, (bool, int)):
                raise serializers.ValidationError(f"O valor de {chave} deve ser texto, inteiro, booleano ou nulo.")
        if len(json.dumps(valor, ensure_ascii=False).encode()) > PAYLOAD_BYTES_MAX:
            raise serializers.ValidationError("O payload passa de 4 KB.")
        # Chaves fechadas não impedem PII no valor: textos passam pela mesma sanitização.
        return {
            chave: sanitizar_texto(item, limite=PAYLOAD_TEXTO_MAX) if isinstance(item, str) else item
            for chave, item in valor.items()
        }

    def validate(self, dados):
        if dados.get("resultado") == ResultadoEvento.ERRO and not dados.get("mensagem_erro"):
            raise serializers.ValidationError({"mensagem_erro": "Obrigatória quando o resultado é erro."})
        if dados.get("id_externo") and not dados.get("sistema_destino"):
            raise serializers.ValidationError({"id_externo": "Informe o sistema_destino junto com o id_externo."})
        return dados


# --------------------------------------------------------------------------- saída
class LeadInternoSerializer(serializers.ModelSerializer):
    """Dados que o n8n precisa para processar o lead (nada além disso)."""

    recebido_em = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Lead
        fields = ["id", "nome", "email", "telefone", "empresa", "cargo", "recebido_em", "status_atual",
                  "classificacao_atual"]
        read_only_fields = fields


class EventoIntegracaoSerializer(serializers.ModelSerializer):
    sistema = serializers.SlugRelatedField(slug_field="codigo", read_only=True)
    lead = serializers.UUIDField(source="lead_id", read_only=True)

    class Meta:
        model = EventoIntegracao
        fields = ["id", "lead", "sistema", "tipo_evento", "resultado", "chave_idempotencia", "id_externo",
                  "ocorrido_em", "recebido_em"]
        read_only_fields = fields


class EventoIntegracaoDetalheSerializer(EventoIntegracaoSerializer):
    class Meta(EventoIntegracaoSerializer.Meta):
        fields = EventoIntegracaoSerializer.Meta.fields + ["mensagem_erro", "payload"]
        read_only_fields = fields


class LeadIntegracaoSerializer(serializers.ModelSerializer):
    sistema = serializers.SlugRelatedField(slug_field="codigo", read_only=True)
    lead = serializers.UUIDField(source="lead_id", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = LeadIntegracao
        fields = ["id", "lead", "sistema", "status", "status_display", "id_externo", "tentativas",
                  "ultima_tentativa_em", "proxima_tentativa_em", "sucesso_em", "ultimo_erro", "created_at",
                  "updated_at"]
        read_only_fields = fields
