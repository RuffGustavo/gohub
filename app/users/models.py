import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.postgres.functions import RandomUUID
from django.core.validators import MinLengthValidator, RegexValidator
from django.db import models
from django.db.models.functions import Length, Lower, Now, Trim
from django.db.models.lookups import GreaterThanOrEqual
from django.utils import timezone

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
    """Usuário interno da área administrativa (tabela "usuario" do MER).

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
        # Todo usuário interno ativo entra na área administrativa (MER, requisito 2.5);
        # o que ele vê e altera depende das permissões atribuídas.
        return self.is_active and self.deleted_at is None

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
        super().save(*args, **kwargs)

    def excluir(self):
        """Exclusão lógica: históricos referenciam o usuário (FK ON DELETE RESTRICT)."""
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])
