"""Integração com o n8n (tabelas "evento_integracao" e "lead_integracao" do MER).

- evento_integracao: log append-only de cada chamada de saída (aplicação -> n8n) e de cada
  retorno recebido (n8n -> aplicação), único por (sistema, chave_idempotencia) (B12).
- lead_integracao: estado atual da integração de um lead com cada sistema.

Gravação só por app/integracoes/services.py. O banco é a última barreira: CHECK com os nomes
do DDL, gatilhos de lead ativo e de imutabilidade (migration 0002_gatilhos).

Divergência deliberada do MER (decisão P7): lead_integracao tem a coluna
proxima_tentativa_em e o índice da fila ix_lead_integracao_fila é
(status, proxima_tentativa_em) em vez de (status, ultima_tentativa_em): o backoff fica
gravado na linha e a consulta do reprocessamento usa o índice diretamente.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q
from django.db.models.functions import Now
from django.db.models.lookups import Exact

from app.leads.models import Lead, Sistema

TIPO_EVENTO_REGEX = r"^[a-z_]+(\.[a-z_]+)*$"
CHAVE_IDEMPOTENCIA_REGEX = r"^[A-Za-z0-9._:-]{8,100}$"
ID_EXTERNO_REGEX = r"^[A-Za-z0-9._:-]{1,100}$"


# --------------------------------------------------------------------------- domínios
class StatusIntegracao(models.TextChoices):
    PENDENTE = "pendente", "Pendente"
    EM_ANDAMENTO = "em_andamento", "Em andamento"
    SUCESSO = "sucesso", "Sucesso"
    ERRO = "erro", "Erro"


class ResultadoEvento(models.TextChoices):
    SUCESSO = "sucesso", "Sucesso"
    ERRO = "erro", "Erro"
    IGNORADO = "ignorado", "Ignorado"


# Catálogo fechado de tipos de evento (decisão P11). Entrada: enviados pelo n8n.
TIPOS_EVENTO_ENTRADA = (
    "classificacao.concluida",
    "crm.sincronizacao",
    "nutricao.inscricao",
    "validacao.concluida",
    "processamento.falha",
)
# Saída: gerados pela aplicação a cada tentativa de webhook.
TIPO_EVENTO_NOTIFICACAO = "lead.notificacao"
TIPOS_EVENTO_SAIDA = (TIPO_EVENTO_NOTIFICACAO,)
TIPOS_EVENTO = TIPOS_EVENTO_ENTRADA + TIPOS_EVENTO_SAIDA

# Nomes dos webhooks enviados ao n8n (campo "evento" do corpo).
WEBHOOK_LEAD_RECEBIDO = "lead.recebido"
WEBHOOK_LEAD_ANONIMIZADO = "lead.anonimizado"
WEBHOOK_TESTE = "integracao.teste"

# Conteúdo permitido em payload (decisão P10, regra A11: sem PII nem credenciais).
PAYLOAD_CHAVES = ("execucao_id", "workflow", "no", "duracao_ms", "codigo_http", "tentativa", "versao")
PAYLOAD_TEXTO_MAX = 200
PAYLOAD_BYTES_MAX = 4 * 1024


# --------------------------------------------------------------------------- evento
class EventoIntegracaoQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Evento de integração é somente inserção: grave um novo registro.")

    def delete(self):
        raise ValidationError("Evento de integração é somente inserção: não pode ser apagado.")


class EventoIntegracao(models.Model):
    """Log imutável das chamadas de integração. O gatilho tg_evento_imutavel recusa UPDATE e
    DELETE; a única exceção é a anonimização do lead (payload e mensagem_erro limpos)."""

    lead = models.ForeignKey(
        Lead, on_delete=models.PROTECT, related_name="eventos_integracao", db_index=False, verbose_name="lead"
    )
    # Coberto pelo índice único uq_evento_idempotencia (sistema_id é a primeira coluna).
    sistema = models.ForeignKey(
        Sistema,
        on_delete=models.PROTECT,
        related_name="eventos_integracao",
        db_index=False,
        verbose_name="sistema",
        help_text="Sistema que originou a chamada (quem enviou o retorno ou quem foi notificado).",
    )
    tipo_evento = models.CharField("tipo do evento", max_length=60)
    resultado = models.CharField("resultado", max_length=10, choices=ResultadoEvento.choices)
    chave_idempotencia = models.CharField(
        "chave de idempotência", max_length=100, help_text="Única por sistema: a repetição não duplica efeitos."
    )
    id_externo = models.CharField("ID externo", max_length=100, null=True, blank=True)
    mensagem_erro = models.CharField(
        "mensagem de erro", max_length=1000, null=True, blank=True, help_text="Obrigatória quando o resultado é erro."
    )
    payload = models.JSONField(
        "resumo técnico", null=True, blank=True, help_text="Somente chaves técnicas permitidas, sem dados pessoais."
    )
    ocorrido_em = models.DateTimeField("ocorrido em", help_text="Instante na origem.")
    recebido_em = models.DateTimeField("recebido em", db_default=Now(), editable=False)

    objects = EventoIntegracaoQuerySet.as_manager()

    class Meta:
        db_table = "evento_integracao"
        db_table_comment = "Log imutável de cada chamada de integração (saída e retorno), idempotente por sistema"
        ordering = ["-ocorrido_em", "-id"]
        verbose_name = "evento de integração"
        verbose_name_plural = "eventos de integração"
        constraints = [
            models.UniqueConstraint(fields=["sistema", "chave_idempotencia"], name="uq_evento_idempotencia"),
            models.CheckConstraint(
                condition=Q(tipo_evento__regex=TIPO_EVENTO_REGEX),
                name="ck_evento_tipo",
                violation_error_message="Tipo de evento fora do padrão.",
            ),
            models.CheckConstraint(
                condition=Q(chave_idempotencia__regex=CHAVE_IDEMPOTENCIA_REGEX),
                name="ck_evento_chave",
                violation_error_message="Chave de idempotência fora do formato permitido.",
            ),
            models.CheckConstraint(
                condition=Q(id_externo__isnull=True) | Q(id_externo__regex=ID_EXTERNO_REGEX),
                name="ck_evento_id_externo",
                violation_error_message="ID externo fora do formato permitido.",
            ),
            models.CheckConstraint(
                condition=~Q(resultado=ResultadoEvento.ERRO) | Q(mensagem_erro__isnull=False),
                name="ck_evento_erro",
                violation_error_message="Evento com erro exige a mensagem de erro.",
            ),
            models.CheckConstraint(
                condition=Q(payload__isnull=True)
                | Exact(Func(F("payload"), function="jsonb_typeof", output_field=models.TextField()), "object"),
                name="ck_evento_payload",
                violation_error_message="O resumo técnico deve ser um objeto JSON.",
            ),
            models.CheckConstraint(
                condition=Q(resultado__in=ResultadoEvento.values),
                name="ck_evento_resultado",
                violation_error_message="Resultado inválido.",
            ),
        ]
        indexes = [
            models.Index(fields=["lead", "-ocorrido_em"], name="ix_evento_lead"),
            # Log do painel (mais recentes primeiro, cursor estável) e contagens das últimas 24 h.
            models.Index(fields=["-recebido_em", "-id"], name="ix_evento_painel"),
        ]

    def __str__(self):
        # Sem dados do lead: __str__ aparece em logs e no admin.
        return f"{self.tipo_evento} [{self.resultado}]"

    def clean(self):
        super().clean()
        if self.resultado == ResultadoEvento.ERRO and not self.mensagem_erro:
            raise ValidationError({"mensagem_erro": "Evento com erro exige a mensagem de erro."})
        if self.payload is not None and not isinstance(self.payload, dict):
            raise ValidationError({"payload": "O resumo técnico deve ser um objeto JSON."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Evento de integração é imutável: grave um novo registro.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Evento de integração é imutável.")


# --------------------------------------------------------------------------- estado
class LeadIntegracao(models.Model):
    """Estado atual da integração de um lead com um sistema (um registro por par)."""

    lead = models.ForeignKey(
        Lead, on_delete=models.PROTECT, related_name="integracoes", db_index=False, verbose_name="lead"
    )
    sistema = models.ForeignKey(
        Sistema, on_delete=models.PROTECT, related_name="integracoes_lead", db_index=False, verbose_name="sistema"
    )
    status = models.CharField(
        "status",
        max_length=20,
        choices=StatusIntegracao.choices,
        default=StatusIntegracao.PENDENTE,
        db_default=StatusIntegracao.PENDENTE,
    )
    id_externo = models.CharField("ID externo", max_length=100, null=True, blank=True)
    # IntegerField + CHECK nomeado (PositiveIntegerField criaria um segundo CHECK sem o nome do DDL).
    tentativas = models.IntegerField("tentativas", default=0, db_default=0)
    ultima_tentativa_em = models.DateTimeField("última tentativa em", null=True, blank=True)
    proxima_tentativa_em = models.DateTimeField(
        "próxima tentativa em", null=True, blank=True, help_text="Vazio: fora da fila automática."
    )
    sucesso_em = models.DateTimeField("sucesso em", null=True, blank=True)
    ultimo_erro = models.CharField(
        "último erro", max_length=1000, null=True, blank=True, help_text="Código do erro, sem dados pessoais."
    )
    created_at = models.DateTimeField("criado em", db_default=Now(), editable=False)
    updated_at = models.DateTimeField("atualizado em", db_default=Now(), editable=False)

    class Meta:
        db_table = "lead_integracao"
        db_table_comment = "Estado atual da integração de cada lead com cada sistema (tentativas e último erro)"
        ordering = ["-updated_at", "-id"]
        verbose_name = "integração do lead"
        verbose_name_plural = "integrações dos leads"
        permissions = [
            ("reprocessar_integracao", "Pode reprocessar integrações"),
            ("gerenciar_integracoes", "Pode ativar sistemas e testar conexões"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["lead", "sistema"], name="uq_lead_integracao_lead_sistema"),
            models.UniqueConstraint(
                fields=["sistema", "id_externo"],
                condition=Q(id_externo__isnull=False),
                name="uq_lead_integracao_id_externo",
            ),
            models.CheckConstraint(
                condition=Q(tentativas__gte=0),
                name="ck_lead_integracao_tentativas",
                violation_error_message="Tentativas não podem ser negativas.",
            ),
            models.CheckConstraint(
                condition=~Q(status=StatusIntegracao.SUCESSO) | Q(sucesso_em__isnull=False),
                name="ck_lead_integracao_sucesso",
                violation_error_message="Integração com sucesso exige a data do sucesso.",
            ),
            models.CheckConstraint(
                condition=Q(tentativas=0) | Q(ultima_tentativa_em__isnull=False),
                name="ck_lead_integracao_tentativa",
                violation_error_message="Tentativas registradas exigem a data da última tentativa.",
            ),
            models.CheckConstraint(
                condition=Q(status__in=StatusIntegracao.values),
                name="ck_lead_integracao_status",
                violation_error_message="Status de integração inválido.",
            ),
            models.CheckConstraint(
                condition=Q(id_externo__isnull=True) | Q(id_externo__regex=ID_EXTERNO_REGEX),
                name="ck_lead_integracao_id_externo",
                violation_error_message="ID externo fora do formato permitido.",
            ),
        ]
        indexes = [
            # Fila de reprocessamento (P7): pendentes/erros cuja próxima tentativa já venceu.
            models.Index(
                fields=["status", "proxima_tentativa_em"],
                condition=Q(status__in=[StatusIntegracao.PENDENTE, StatusIntegracao.ERRO]),
                name="ix_lead_integracao_fila",
            ),
        ]

    def __str__(self):
        return f"{self.sistema_id}: {self.get_status_display()}"

    def clean(self):
        super().clean()
        if self.status == StatusIntegracao.SUCESSO and not self.sucesso_em:
            raise ValidationError({"sucesso_em": "Integração com sucesso exige a data do sucesso."})
        if self.tentativas and not self.ultima_tentativa_em:
            raise ValidationError({"ultima_tentativa_em": "Tentativas registradas exigem a data da última tentativa."})
        if self.tentativas < 0:
            raise ValidationError({"tentativas": "Tentativas não podem ser negativas."})
