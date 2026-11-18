import re
import uuid

from django.conf import settings
from django.contrib.postgres.functions import RandomUUID
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.models import Q
from django.db.models.functions import Length, Lower, Now, Trim
from django.db.models.lookups import GreaterThanOrEqual

# Regras do DDL (.knowledge/leads_ddl.sql), repetidas aqui para validação amigável;
# o banco continua sendo a última barreira (CHECK e gatilhos).
EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
TELEFONE_E164_REGEX = r"^\+[1-9][0-9]{7,14}$"
SISTEMA_CODIGO_REGEX = r"^[a-z][a-z0-9_]{1,29}$"


# --------------------------------------------------------------------------- domínios
# Decisão P3: o DDL usa ENUM nativo; aqui são TextChoices + CheckConstraint (migrations
# do Django funcionam e os gatilhos comparam texto). Para incluir um valor, altere a
# classe e gere uma migration (o CHECK de domínio é recriado).
class LeadStatus(models.TextChoices):
    RECEBIDO = "recebido", "Recebido"
    ENVIADO_CRM = "enviado_crm", "Enviado ao CRM"
    EM_NUTRICAO = "em_nutricao", "Em nutrição"
    DUPLICADO = "duplicado", "Duplicado"
    INVALIDO = "invalido", "Inválido"
    ERRO = "erro", "Erro"


class ClassificacaoLead(models.TextChoices):
    FRIO = "frio", "Frio"
    MORNO = "morno", "Morno"
    QUENTE = "quente", "Quente"


class MetodoClassificacao(models.TextChoices):
    REGRA = "regra", "Regra fixa"
    IA = "ia", "Inteligência artificial"
    MANUAL = "manual", "Manual"


# --------------------------------------------------------------------------- sistema
class Sistema(models.Model):
    """Sistemas que originam ações (tabela "sistema" do MER).

    Decisão P1: fica no app de leads enquanto ele for o único a usá-la; o futuro app
    "integracoes" a referencia daqui. Dados iniciais na migration 0002.
    """

    APLICACAO_WEB = "aplicacao_web"
    N8N = "n8n"
    CRM = "crm"
    NOTIFICACAO = "notificacao"

    id = models.SmallAutoField(primary_key=True)
    codigo = models.CharField(
        "código",
        max_length=30,
        help_text="Código estável: letras minúsculas, números e sublinhado (ex.: aplicacao_web).",
    )
    nome = models.CharField("nome", max_length=100)
    ativo = models.BooleanField("ativo", default=True, db_default=True)
    created_at = models.DateTimeField("criado em", db_default=Now(), editable=False)
    updated_at = models.DateTimeField("atualizado em", db_default=Now(), editable=False)

    class Meta:
        db_table = "sistema"
        db_table_comment = "Domínio dos sistemas que originam ações (rastreabilidade)"
        ordering = ["codigo"]
        verbose_name = "sistema"
        verbose_name_plural = "sistemas"
        constraints = [
            models.UniqueConstraint(fields=["codigo"], name="uq_sistema_codigo"),
            models.CheckConstraint(
                condition=Q(codigo__regex=SISTEMA_CODIGO_REGEX),
                name="ck_sistema_codigo",
                violation_error_message="Código fora do formato permitido.",
            ),
        ]

    def __str__(self):
        return self.nome

    @classmethod
    def por_codigo(cls, codigo):
        return cls.objects.get(codigo=codigo)


# --------------------------------------------------------------------------- lead
class LeadQuerySet(models.QuerySet):
    def ativos(self):
        """Leads não excluídos (usa os índices parciais WHERE deleted_at IS NULL)."""
        return self.filter(deleted_at__isnull=True)

    def delete(self):
        raise ValidationError("Leads não são apagados: use a anonimização (LGPD).")


class Lead(models.Model):
    """Lead captado pelo formulário público (tabela "lead" do MER).

    status_atual/classificacao_atual são um snapshot mantido pelo banco a partir de
    lead_status_historico e lead_classificacao: o gatilho fn_lead_proteger_snapshot recusa
    UPDATE direto. Gravação só pelos services (app/leads/services.py).
    """

    # Nunca regravados por save() em UPDATE: o banco é dono desses valores.
    SNAPSHOT_FIELDS = frozenset(
        {"status_atual", "status_atualizado_em", "classificacao_atual", "classificado_em"}
    )
    DB_MANAGED_FIELDS = frozenset({"created_at", "updated_at", "deleted_at", "anonimizado_em"})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=RandomUUID(), editable=False)
    nome = models.CharField("nome", max_length=150)
    email = models.EmailField(
        "e-mail",
        max_length=254,
        db_comment="E-mail normalizado (trim + minúsculas); único entre leads não excluídos",
    )
    telefone = models.CharField(
        "telefone",
        max_length=20,
        help_text="Formato E.164, ex.: +5511987654321.",
        db_comment="Telefone em formato E.164, ex.: +5511987654321",
    )
    empresa = models.CharField("empresa", max_length=150)
    cargo = models.CharField("cargo", max_length=100)
    status_atual = models.CharField(
        "status", max_length=20, choices=LeadStatus.choices, db_default=LeadStatus.RECEBIDO, editable=False
    )
    status_atualizado_em = models.DateTimeField("status desde", db_default=Now(), editable=False)
    classificacao_atual = models.CharField(
        "classificação",
        max_length=10,
        choices=ClassificacaoLead.choices,
        null=True,
        blank=True,
        editable=False,
        help_text="Vazio: ainda não classificado.",
    )
    classificado_em = models.DateTimeField("classificado em", null=True, blank=True, editable=False)
    consentimento_em = models.DateTimeField("aceite da política em", editable=False)
    politica_versao = models.CharField("versão da política", max_length=20, editable=False)
    created_at = models.DateTimeField("recebido em", db_default=Now(), editable=False)
    updated_at = models.DateTimeField("atualizado em", db_default=Now(), editable=False)
    deleted_at = models.DateTimeField("excluído em", null=True, blank=True, editable=False)
    anonimizado_em = models.DateTimeField("anonimizado em", null=True, blank=True, editable=False)

    objects = LeadQuerySet.as_manager()

    class Meta:
        db_table = "lead"
        db_table_comment = (
            "Lead captado pelo formulário público; status_atual e classificacao_atual são "
            "snapshot mantido por gatilho a partir dos históricos"
        )
        ordering = ["-created_at", "-id"]
        verbose_name = "lead"
        verbose_name_plural = "leads"
        permissions = [
            ("reclassificar_lead", "Pode reclassificar leads manualmente"),
            ("anonimizar_lead", "Pode anonimizar dados pessoais de leads (LGPD)"),
        ]
        constraints = [
            # Índice único parcial: duplicidade barrada inclusive sob concorrência (B4).
            models.UniqueConstraint(
                fields=["email"], condition=Q(deleted_at__isnull=True), name="uq_lead_email_ativo"
            ),
            models.CheckConstraint(
                condition=Q(anonimizado_em__isnull=False) | GreaterThanOrEqual(Length(Trim("nome")), 2),
                name="ck_lead_nome",
                violation_error_message="O nome deve ter ao menos 2 caracteres.",
            ),
            models.CheckConstraint(
                condition=Q(anonimizado_em__isnull=False)
                | (Q(email=Lower(Trim("email"))) & Q(email__regex=EMAIL_REGEX)),
                name="ck_lead_email",
                violation_error_message="Informe um e-mail válido.",
            ),
            models.CheckConstraint(
                condition=Q(anonimizado_em__isnull=False) | Q(telefone__regex=TELEFONE_E164_REGEX),
                name="ck_lead_telefone",
                violation_error_message="Telefone fora do formato E.164.",
            ),
            models.CheckConstraint(
                condition=GreaterThanOrEqual(Length(Trim("empresa")), 1),
                name="ck_lead_empresa",
                violation_error_message="Informe a empresa.",
            ),
            models.CheckConstraint(
                condition=GreaterThanOrEqual(Length(Trim("cargo")), 1),
                name="ck_lead_cargo",
                violation_error_message="Informe o cargo.",
            ),
            models.CheckConstraint(
                condition=Q(classificacao_atual__isnull=True, classificado_em__isnull=True)
                | Q(classificacao_atual__isnull=False, classificado_em__isnull=False),
                name="ck_lead_classificacao_par",
                violation_error_message="Classificação e data da classificação andam juntas.",
            ),
            models.CheckConstraint(
                condition=Q(anonimizado_em__isnull=True) | Q(deleted_at__isnull=False),
                name="ck_lead_anonimizado_excluido",
                violation_error_message="Lead anonimizado precisa estar excluído.",
            ),
            models.CheckConstraint(
                condition=Q(status_atual__in=LeadStatus.values),
                name="ck_lead_status_atual",
                violation_error_message="Status inválido.",
            ),
            models.CheckConstraint(
                condition=Q(classificacao_atual__isnull=True)
                | Q(classificacao_atual__in=ClassificacaoLead.values),
                name="ck_lead_classificacao_atual",
                violation_error_message="Classificação inválida.",
            ),
        ]
        indexes = [
            # Entregável 7: listagem padrão e paginação por cursor (keyset).
            models.Index(
                fields=["-created_at", "-id"], condition=Q(deleted_at__isnull=True), name="ix_lead_listagem"
            ),
            models.Index(
                fields=["status_atual", "-created_at"],
                condition=Q(deleted_at__isnull=True),
                name="ix_lead_status_periodo",
            ),
            models.Index(
                fields=["classificacao_atual", "-created_at"],
                condition=Q(deleted_at__isnull=True),
                name="ix_lead_classificacao_periodo",
            ),
        ]

    def __str__(self):
        # Sem e-mail: __str__ aparece em logs, mensagens do admin e lookups.
        return self.nome

    @property
    def recebido_em(self):
        return self.created_at

    def _normalizar(self):
        """Mantém em memória o mesmo valor que o gatilho fn_lead_normalizar grava."""
        if self.anonimizado_em is not None:
            return
        from .validators import normalizar_texto  # evita import circular

        self.nome = normalizar_texto(self.nome)
        self.empresa = normalizar_texto(self.empresa)
        self.cargo = normalizar_texto(self.cargo)
        self.email = (self.email or "").strip().lower()
        self.telefone = re.sub(r"[^0-9+]", "", self.telefone or "")

    def clean(self):
        super().clean()
        self._normalizar()

    def save(self, *args, **kwargs):
        self._normalizar()
        # Em UPDATE nunca regrava o snapshot nem colunas mantidas pelo banco: evita
        # sobrescrever um status mais novo com um valor lido antes (condição de corrida).
        if not self._state.adding:
            campos = kwargs.get("update_fields")
            if campos is None:
                campos = [f.name for f in self._meta.concrete_fields if not f.primary_key]
            kwargs["update_fields"] = [
                nome for nome in campos if nome not in self.SNAPSHOT_FIELDS | self.DB_MANAGED_FIELDS
            ]
            if not kwargs["update_fields"]:
                return
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Leads não são apagados: use a anonimização (LGPD).")

    def anonimizar(self):
        """Elimina os dados pessoais (B15) mantendo os históricos; use o service."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT fn_anonimizar_lead(%s)", [self.pk])
        self.refresh_from_db()


# --------------------------------------------------------------------------- históricos
class _RegistroImutavelQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Histórico é somente inserção: grave um novo registro.")

    def delete(self):
        raise ValidationError("Histórico é somente inserção: não pode ser apagado.")


class _RegistroImutavel(models.Model):
    """Base das tabelas append-only (o gatilho fn_bloquear_alteracao recusa UPDATE/DELETE).

    Decisão P2: a FK evento_id (-> evento_integracao) do MER entrou com o app "integracoes"
    (migration leads.0004_evento_integracao, que depende de integracoes.0001).
    """

    objects = _RegistroImutavelQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(f"{self._meta.verbose_name} é imutável: grave um novo registro.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self._meta.verbose_name} é imutável.")

    @property
    def autor(self):
        """Username do usuário ou código do sistema responsável (nunca dados pessoais)."""
        if self.usuario_id:
            return self.usuario.username
        return self.sistema.codigo if self.sistema_id else None

    def _validar_autor(self):
        if (self.sistema_id is None) == (self.usuario_id is None):
            raise ValidationError("Informe exatamente um autor: sistema ou usuário.")


def _autor_unico(prefixo):
    return models.CheckConstraint(
        condition=Q(sistema__isnull=False, usuario__isnull=True) | Q(sistema__isnull=True, usuario__isnull=False),
        name=f"ck_{prefixo}_autor",
        violation_error_message="Informe exatamente um autor: sistema ou usuário.",
    )


class LeadClassificacao(_RegistroImutavel):
    """Histórico de classificações (tabela "lead_classificacao"); inserir aqui atualiza
    o snapshot classificacao_atual do lead (gatilho fn_classificacao_aplicar)."""

    lead = models.ForeignKey(
        Lead, on_delete=models.PROTECT, related_name="classificacoes", db_index=False, verbose_name="lead"
    )
    classificacao = models.CharField("classificação", max_length=10, choices=ClassificacaoLead.choices)
    metodo = models.CharField("método", max_length=10, choices=MetodoClassificacao.choices)
    justificativa = models.CharField(
        "justificativa", max_length=2000, null=True, blank=True, help_text="Motivo, sem dados pessoais."
    )
    versao_criterio = models.CharField("versão da regra/modelo", max_length=50, null=True, blank=True)
    confianca = models.DecimalField(
        "confiança", max_digits=5, decimal_places=4, null=True, blank=True, help_text="Só para IA, entre 0 e 1."
    )
    # Sem índice nas FKs para sistema: poucas linhas e sem DELETE (Entregável 7).
    sistema = models.ForeignKey(
        Sistema,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="classificacoes_lead",
        db_index=False,
        verbose_name="sistema",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="classificacoes_lead",
        db_index=False,
        verbose_name="usuário",
    )
    evento = models.ForeignKey(
        "integracoes.EventoIntegracao",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="classificacoes",
        db_index=False,  # índice parcial ix_classificacao_evento
        verbose_name="evento de integração",
    )
    classificado_em = models.DateTimeField("classificado em", db_default=Now())
    created_at = models.DateTimeField("gravado em", db_default=Now(), editable=False)

    class Meta:
        db_table = "lead_classificacao"
        db_table_comment = "Histórico imutável de classificações do lead (fonte da verdade da classificação)"
        ordering = ["-classificado_em", "-id"]
        verbose_name = "classificação do lead"
        verbose_name_plural = "classificações dos leads"
        constraints = [
            _autor_unico("classificacao"),
            models.CheckConstraint(
                condition=~Q(metodo=MetodoClassificacao.MANUAL) | Q(usuario__isnull=False),
                name="ck_classificacao_manual",
                violation_error_message="Reclassificação manual exige o usuário responsável.",
            ),
            models.CheckConstraint(
                condition=Q(metodo=MetodoClassificacao.MANUAL) | Q(sistema__isnull=False),
                name="ck_classificacao_automatica",
                violation_error_message="Classificação por regra ou IA exige o sistema responsável.",
            ),
            models.CheckConstraint(
                condition=Q(confianca__isnull=True)
                | Q(metodo=MetodoClassificacao.IA, confianca__gte=0, confianca__lte=1),
                name="ck_classificacao_confianca",
                violation_error_message="Confiança só para IA, entre 0 e 1.",
            ),
            models.CheckConstraint(
                condition=Q(classificacao__in=ClassificacaoLead.values),
                name="ck_classificacao_valor",
                violation_error_message="Classificação inválida.",
            ),
            models.CheckConstraint(
                condition=Q(metodo__in=MetodoClassificacao.values),
                name="ck_classificacao_metodo",
                violation_error_message="Método inválido.",
            ),
        ]
        indexes = [
            models.Index(fields=["lead", "-classificado_em"], name="ix_classificacao_lead"),
            models.Index(fields=["usuario"], condition=Q(usuario__isnull=False), name="ix_classificacao_usuario"),
            models.Index(fields=["evento"], condition=Q(evento__isnull=False), name="ix_classificacao_evento"),
        ]

    def __str__(self):
        return f"{self.get_classificacao_display()} ({self.get_metodo_display()})"

    def clean(self):
        super().clean()
        self._validar_autor()
        if self.metodo == MetodoClassificacao.MANUAL and not self.usuario_id:
            raise ValidationError("Reclassificação manual exige o usuário responsável.")
        if self.metodo != MetodoClassificacao.MANUAL and not self.sistema_id:
            raise ValidationError("Classificação por regra ou IA exige o sistema responsável.")
        if self.confianca is not None:
            if self.metodo != MetodoClassificacao.IA:
                raise ValidationError({"confianca": "Confiança só se aplica a classificações por IA."})
            if not 0 <= self.confianca <= 1:
                raise ValidationError({"confianca": "A confiança deve estar entre 0 e 1."})


class LeadStatusHistorico(_RegistroImutavel):
    """Histórico de status (tabela "lead_status_historico"); status_anterior é preenchido
    pelo banco e inserir aqui atualiza o snapshot status_atual (gatilhos fn_status_hist_*)."""

    lead = models.ForeignKey(
        Lead, on_delete=models.PROTECT, related_name="historico_status", db_index=False, verbose_name="lead"
    )
    status_anterior = models.CharField(
        "status anterior", max_length=20, choices=LeadStatus.choices, null=True, blank=True, editable=False
    )
    status_novo = models.CharField("status novo", max_length=20, choices=LeadStatus.choices)
    sistema = models.ForeignKey(
        Sistema,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historico_status_lead",
        db_index=False,
        verbose_name="sistema",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historico_status_lead",
        db_index=False,
        verbose_name="usuário",
    )
    evento = models.ForeignKey(
        "integracoes.EventoIntegracao",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historico_status",
        db_index=False,  # índice parcial ix_status_hist_evento
        verbose_name="evento de integração",
    )
    observacao = models.CharField("observação", max_length=1000, null=True, blank=True)
    ocorrido_em = models.DateTimeField(
        "ocorrido em", db_default=Now(), help_text="Instante na origem (ordena eventos fora de ordem)."
    )
    created_at = models.DateTimeField("gravado em", db_default=Now(), editable=False)

    class Meta:
        db_table = "lead_status_historico"
        db_table_comment = "Histórico imutável de mudanças de status do lead (fonte da verdade do status)"
        ordering = ["-ocorrido_em", "-id"]
        verbose_name = "mudança de status"
        verbose_name_plural = "histórico de status"
        constraints = [
            _autor_unico("status_hist"),
            models.CheckConstraint(
                condition=~Q(status_novo=LeadStatus.RECEBIDO) | Q(status_anterior__isnull=True),
                name="ck_status_hist_inicial",
                violation_error_message="O status recebido só pode ser o primeiro registro.",
            ),
            models.CheckConstraint(
                condition=Q(status_novo__in=LeadStatus.values),
                name="ck_status_hist_status_novo",
                violation_error_message="Status inválido.",
            ),
            models.CheckConstraint(
                condition=Q(status_anterior__isnull=True) | Q(status_anterior__in=LeadStatus.values),
                name="ck_status_hist_status_anterior",
                violation_error_message="Status anterior inválido.",
            ),
        ]
        indexes = [
            models.Index(fields=["lead", "-ocorrido_em"], name="ix_status_hist_lead"),
            models.Index(fields=["usuario"], condition=Q(usuario__isnull=False), name="ix_status_hist_usuario"),
            models.Index(fields=["evento"], condition=Q(evento__isnull=False), name="ix_status_hist_evento"),
        ]

    def __str__(self):
        anterior = self.get_status_anterior_display() if self.status_anterior else "—"
        return f"{anterior} → {self.get_status_novo_display()}"

    def clean(self):
        super().clean()
        self._validar_autor()
