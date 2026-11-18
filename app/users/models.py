import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.postgres.functions import RandomUUID
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, RegexValidator
from django.db import models, transaction
from django.db.models import Exists, OuterRef
from django.db.models.functions import Length, Lower, Now, Trim
from django.db.models.lookups import GreaterThanOrEqual
from django.utils import timezone
from django.utils.crypto import salted_hmac

# Regras do DDL (.knowledge/leads_ddl.sql, tabela "usuario"), repetidas aqui para
# validação amigável no formulário; o banco continua sendo a última barreira.
USERNAME_REGEX = r"^[a-z0-9._-]{3,50}\Z"
EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# Prefixos de hash aceitos pelo CHECK ck_usuario_senha_hash: formato do Django
# (argon2, pbkdf2_sha256) e os formatos originais do DDL ($argon2id$ e bcrypt $2a/b/y$).
SENHA_HASH_REGEX = r"^(argon2\$argon2id\$|pbkdf2_sha256\$|\$argon2id\$|\$2[aby]\$)"


def normalizar_username(valor):
    """Mesma regra do gatilho fn_usuario_normalizar: trim + minúsculas."""
    return (valor or "").strip().lower()


def normalizar_email(valor):
    """Mesma regra do gatilho fn_usuario_normalizar: trim + minúsculas."""
    return (valor or "").strip().lower()


def normalizar_nome(valor):
    """Mesma regra do gatilho fn_usuario_normalizar: espaços repetidos viram um só."""
    return " ".join((valor or "").split())


class UsuarioQuerySet(models.QuerySet):
    def ativos(self):
        return self.filter(is_active=True, deleted_at__isnull=True)

    def sem_privilegio(self):
        """Usuários sem nenhum privilégio (ver Usuario.tem_privilegio)."""
        grupos = self.model.groups.through.objects.filter(usuario_id=OuterRef("pk"))
        permissoes = self.model.user_permissions.through.objects.filter(usuario_id=OuterRef("pk"))
        return self.filter(is_superuser=False).exclude(Exists(grupos)).exclude(Exists(permissoes))


class UsuarioManager(BaseUserManager.from_queryset(UsuarioQuerySet)):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        # Usuário excluído logicamente não autentica (regra A6 do MER).
        return self.get(username=normalizar_username(username), deleted_at__isnull=True)

    def _criar(self, username, email, nome, password, **extra_fields):
        if not password:
            # O CHECK ck_usuario_senha_hash recusa senha vazia ou inutilizável.
            raise ValueError("Informe uma senha.")
        usuario = self.model(username=username, email=email, nome=nome, **extra_fields)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, username, email, nome, password=None, **extra_fields):
        extra_fields["is_superuser"] = False
        return self._criar(username, email, nome, password, **extra_fields)

    def create_superuser(self, username, email, nome, password=None, **extra_fields):
        extra_fields["is_superuser"] = True
        return self._criar(username, email, nome, password, **extra_fields)


class Usuario(AbstractBaseUser, PermissionsMixin):
    """Usuário interno do sistema (tabela "usuario" do MER).

    Entra pela área de acesso (users:login); o Django Admin fica restrito a quem tem
    is_staff (ver a propriedade abaixo).

    Colunas do DDL mapeadas para os nomes que o Django espera:
    password → senha_hash, last_login → ultimo_login_em, is_active → ativo.
    Fora do DDL (decisão registrada): is_superuser (coluna superusuario) e as tabelas
    usuario_groups/usuario_user_permissions, trazidas pelo PermissionsMixin.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=RandomUUID(), editable=False)
    nome = models.CharField("nome", max_length=150, validators=[MinLengthValidator(2)])
    username = models.CharField(
        "nome de usuário",
        max_length=50,
        validators=[
            RegexValidator(
                USERNAME_REGEX,
                "Use de 3 a 50 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado.",
            )
        ],
        help_text="De 3 a 50 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado.",
        error_messages={"unique": "Já existe um usuário com este nome de usuário."},
    )
    email = models.EmailField(
        "e-mail", max_length=254, error_messages={"unique": "Já existe um usuário com este e-mail."}
    )
    password = models.CharField("senha (hash)", max_length=255, db_column="senha_hash")
    is_active = models.BooleanField(
        "ativo",
        default=True,
        db_default=True,
        db_column="ativo",
        help_text="Desmarque para bloquear o acesso sem excluir o usuário.",
    )
    is_superuser = models.BooleanField(
        "superusuário",
        default=False,
        db_default=False,
        db_column="superusuario",
        help_text="Tem todas as permissões sem precisar atribuí-las.",
    )
    last_login = models.DateTimeField("último login", blank=True, null=True, db_column="ultimo_login_em")
    created_at = models.DateTimeField("criado em", db_default=Now(), editable=False)
    updated_at = models.DateTimeField("atualizado em", db_default=Now(), editable=False)
    deleted_at = models.DateTimeField("excluído em", blank=True, null=True, editable=False)

    objects = UsuarioManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["email", "nome"]

    class Meta:
        db_table = "usuario"
        db_table_comment = "Usuários internos da área administrativa; senha somente como hash Argon2id/bcrypt"
        ordering = ["nome"]
        verbose_name = "usuário"
        verbose_name_plural = "usuários"
        permissions = [
            ("acessar_admin", "Pode acessar a área administrativa (Django Admin)"),
        ]
        constraints = [
            # Sem violation_error_message: assim o erro fica no campo, com error_messages["unique"].
            models.UniqueConstraint(fields=["username"], name="uq_usuario_username"),
            # Sem violation_error_message: assim o erro fica no campo, com error_messages["unique"].
            models.UniqueConstraint(fields=["email"], name="uq_usuario_email"),
            models.CheckConstraint(
                condition=GreaterThanOrEqual(Length(Trim("nome")), 2),
                name="ck_usuario_nome",
                violation_error_message="O nome deve ter ao menos 2 caracteres.",
            ),
            models.CheckConstraint(
                condition=models.Q(username__regex=USERNAME_REGEX.replace(r"\Z", "$")),
                name="ck_usuario_username",
                violation_error_message="Nome de usuário fora do formato permitido.",
            ),
            models.CheckConstraint(
                condition=models.Q(email=Lower(Trim("email"))) & models.Q(email__regex=EMAIL_REGEX),
                name="ck_usuario_email",
                violation_error_message="Informe um e-mail válido.",
            ),
            models.CheckConstraint(
                condition=models.Q(password__regex=SENHA_HASH_REGEX),
                name="ck_usuario_senha_hash",
                violation_error_message="A senha só pode ser gravada como hash.",
            ),
            models.CheckConstraint(
                condition=models.Q(deleted_at__isnull=True) | models.Q(is_active=False),
                name="ck_usuario_excluido_inativo",
                violation_error_message="Usuário excluído precisa estar inativo.",
            ),
        ]

    def __str__(self):
        return self.username

    @property
    def is_staff(self):
        """Pode entrar no Django Admin (o admin exige is_active e is_staff).

        Só superusuários e quem tem a permissão "users.acessar_admin" (direta ou por grupo).
        Os demais usuários usam a área de acesso do sistema (users:login). O que cada um vê
        e altera no admin continua dependendo das permissões de model atribuídas.
        Conceder a permissão é restrito a superusuários (CAMPOS_DE_PRIVILEGIO no admin).
        """
        if not self.is_active or self.deleted_at is not None:
            return False
        return self.is_superuser or self.has_perm("users.acessar_admin")

    def tem_privilegio(self):
        """Superusuário, membro de algum grupo ou com alguma permissão direta.

        Inclui o acesso ao admin (is_staff deriva de superusuário ou da permissão
        "users.acessar_admin"). Só superusuários alteram contas com privilégio (R2).
        """
        if self.is_superuser or self.pk is None:
            return self.is_superuser
        return self.groups.exists() or self.user_permissions.exists()

    def _get_session_auth_hash(self, secret=None):
        # O Django deriva o hash da sessão só da senha; incluir o e-mail faz a troca de
        # e-mail também encerrar todas as sessões abertas da pessoa (a troca de senha já encerra).
        return salted_hmac(
            "app.users.models.Usuario.get_session_auth_hash",
            f"{self.password}\x00{self.email}",
            secret=secret,
            algorithm="sha256",
        ).hexdigest()

    @classmethod
    def from_db(cls, db, field_names, values):
        instancia = super().from_db(db, field_names, values)
        # Valor gravado no banco, para detectar a troca de e-mail no save().
        instancia._email_no_banco = getattr(instancia, "email", None) if "email" in field_names else None
        return instancia

    def get_full_name(self):
        return self.nome

    def get_short_name(self):
        return self.nome.split(" ")[0] if self.nome else self.username

    def clean(self):
        super().clean()
        self.username = normalizar_username(self.username)
        self.email = normalizar_email(self.email)
        self.nome = normalizar_nome(self.nome)

    def save(self, *args, **kwargs):
        # Mantém em memória o mesmo valor que o gatilho fn_usuario_normalizar grava.
        self.username = normalizar_username(self.username)
        self.email = normalizar_email(self.email)
        self.nome = normalizar_nome(self.nome)
        email_antigo = getattr(self, "_email_no_banco", None)
        update_fields = kwargs.get("update_fields")
        email_mudou = (
            email_antigo is not None
            and email_antigo != self.email
            and (update_fields is None or "email" in update_fields)
        )
        # _password só fica preenchido após set_password(); o rehash automático do
        # check_password o zera antes de salvar, então não conta como troca de senha.
        senha_mudou = (
            not self._state.adding
            and self._password is not None
            and (update_fields is None or "password" in update_fields)
        )
        super().save(*args, **kwargs)
        if email_mudou or senha_mudou:
            self._agendar_avisos(email_antigo if email_mudou else None, senha_mudou)
        self._email_no_banco = self.email

    def _agendar_avisos(self, email_antigo, senha_mudou):
        """Registra e avisa o titular depois do commit (nada é enviado se a transação falhar)."""
        from .auditoria import registrar
        from .avisos import avisar_troca_de_email, avisar_troca_de_senha

        def avisar():
            if email_antigo:
                registrar("email_alterado", usuario=self)
                avisar_troca_de_email(self, email_antigo)
            if senha_mudou:
                registrar("senha_alterada", usuario=self)
                avisar_troca_de_senha(self)

        transaction.on_commit(avisar)

    def excluir(self):
        """Exclusão lógica: históricos referenciam o usuário (FK ON DELETE RESTRICT)."""
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])


# --------------------------------------------------------------------------- acessos
class StatusAcesso(models.TextChoices):
    SUCESSO = "sucesso", "Sucesso"
    FALHA = "falha", "Falha"


class CanalAcesso(models.TextChoices):
    PAINEL = "painel", "Painel"
    ADMIN = "admin", "Área administrativa"
    API = "api", "API"


class DispositivoAcesso(models.TextChoices):
    COMPUTADOR = "computador", "Computador"
    CELULAR = "celular", "Celular"
    TABLET = "tablet", "Tablet"
    OUTRO = "outro", "Outro / não identificado"


class RegistroAcessoQuerySet(models.QuerySet):
    def sucessos(self):
        return self.filter(status=StatusAcesso.SUCESSO)

    def update(self, **kwargs):
        raise ValidationError("Registro de acesso é somente inserção.")


class RegistroAcesso(models.Model):
    """Tentativa de login de um usuário existente (sucesso ou falha), gravada pelos receptores
    dos sinais de autenticação (app/users/auditoria.py).

    Minimização (LGPD): não guarda o User-Agent completo, só navegador, sistema e tipo de
    dispositivo resumidos (app/users/agente.py). O IP é dado pessoal; tentativa com usuário
    inexistente não é gravada (não há titular a quem mostrar).
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="acessos",
        db_index=False,  # coberto pelo índice ix_acesso_usuario_data
        verbose_name="usuário",
    )
    status = models.CharField("status", max_length=10, choices=StatusAcesso.choices)
    canal = models.CharField("canal", max_length=10, choices=CanalAcesso.choices)
    ip = models.GenericIPAddressField("endereço IP", null=True, blank=True)
    navegador = models.CharField("navegador", max_length=30, blank=True, default="")
    sistema_operacional = models.CharField("sistema operacional", max_length=30, blank=True, default="")
    dispositivo = models.CharField(
        "dispositivo", max_length=12, choices=DispositivoAcesso.choices, default=DispositivoAcesso.OUTRO
    )
    ocorrido_em = models.DateTimeField("ocorrido em", db_default=Now(), editable=False)

    objects = RegistroAcessoQuerySet.as_manager()

    class Meta:
        db_table = "registro_acesso"
        db_table_comment = "Histórico imutável de tentativas de login (sucesso e falha) de usuários existentes"
        ordering = ["-ocorrido_em", "-id"]
        verbose_name = "registro de acesso"
        verbose_name_plural = "registros de acesso"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=StatusAcesso.values), name="ck_acesso_status",
                violation_error_message="Status inválido.",
            ),
            models.CheckConstraint(
                condition=models.Q(canal__in=CanalAcesso.values), name="ck_acesso_canal",
                violation_error_message="Canal inválido.",
            ),
            models.CheckConstraint(
                condition=models.Q(dispositivo__in=DispositivoAcesso.values), name="ck_acesso_dispositivo",
                violation_error_message="Dispositivo inválido.",
            ),
        ]
        indexes = [
            # Dashboard pessoal (sempre filtrado pelo usuário) e visão geral por período.
            models.Index(fields=["usuario", "-ocorrido_em"], name="ix_acesso_usuario_data"),
            models.Index(fields=["-ocorrido_em"], name="ix_acesso_data"),
        ]

    def __str__(self):
        return f"{self.get_status_display()} em {self.ocorrido_em:%d/%m/%Y %H:%M}" if self.ocorrido_em else "Acesso"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Registro de acesso é imutável: grave um novo registro.")
        super().save(*args, **kwargs)
