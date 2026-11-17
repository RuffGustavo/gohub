"""Regras de escrita do domínio de leads: única porta de gravação (telas, API e admin).

O snapshot status_atual/classificacao_atual nunca é escrito aqui: muda por gatilho ao
inserir nos históricos. Auditoria no logger "gohub.auditoria" só com ids; o IP do visitante (dado pessoal) entra no
evento lead_captado e segue a retenção dos logs da infraestrutura, não a anonimização do lead.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, DataError, IntegrityError, connection, transaction
from django.utils import timezone

from app.users.auditoria import registrar

from .models import LeadClassificacao, MetodoClassificacao
from .validators import normalizar_email, normalizar_justificativa, normalizar_obrigatorio, normalizar_telefone

# Parametrizado (nunca f-string). A unicidade vem do índice parcial uq_lead_email_ativo:
# o ON CONFLICT garante 1 lead por e-mail ativo mesmo sob concorrência (A3, B4), sem
# SELECT prévio (condição de corrida) nem SELECT posterior no caso duplicado (D2: o
# caminho duplicado não faz trabalho extra que diferencie a resposta).
_SQL_INSERIR_LEAD = """
    INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (email) WHERE deleted_at IS NULL DO NOTHING
    RETURNING id
"""


def _politica_versao():
    return settings.POLITICA_PRIVACIDADE_VERSAO


def _ao_receber_lead(lead_id):
    """Ponto de extensão (P14), chamado depois do commit de um lead novo.

    Sem efeito por enquanto. Quando o app "integracoes" existir, dispare aqui a task
    Celery passando só o id: `notificar_lead_recebido.delay(str(lead_id))`.
    """


def registrar_lead(*, nome, email, telefone, empresa, cargo, request=None):
    """Cadastra o lead com o aceite da política vigente; e-mail já ativo não faz nada.

    Retorna (lead_id, criado); no caso duplicado, (None, False). Quem chama deve dar ao
    visitante a mesma resposta nos dois casos (não revela quais e-mails estão cadastrados).
    Levanta ValidationError se algum dado for inválido.
    """
    valores = [
        normalizar_obrigatorio(nome, minimo=2, mensagem="Informe o nome."),
        normalizar_email(email),
        normalizar_telefone(telefone),
        normalizar_obrigatorio(empresa, mensagem="Informe a empresa."),
        normalizar_obrigatorio(cargo, mensagem="Informe o cargo."),
        timezone.now(),
        _politica_versao(),
    ]
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(_SQL_INSERIR_LEAD, valores)
            linha = cursor.fetchone()
    except (IntegrityError, DataError):
        # Último filtro do banco (CHECK ck_lead_*) recusou algo que a validação aceitou.
        raise ValidationError("Não foi possível registrar os dados informados.") from None
    if linha is None:
        return None, False
    lead_id = linha[0]
    # Fora de transação o on_commit roda na hora; dentro de uma (ATOMIC_REQUESTS, testes), no commit.
    transaction.on_commit(lambda: _ao_receber_lead(lead_id))
    transaction.on_commit(lambda: registrar("lead_captado", request, lead_id=lead_id))
    return lead_id, True


def reclassificar_lead(*, lead, classificacao, justificativa, usuario, request=None):
    """Reclassificação manual (método sempre "manual", autor sempre `usuario`).

    O registro anterior é preservado; o snapshot do lead muda pelo gatilho
    fn_classificacao_aplicar. O lead deve estar ativo (o gatilho fn_exigir_lead_ativo
    recusa os excluídos).
    """
    registro = LeadClassificacao(
        lead=lead,
        classificacao=classificacao,
        metodo=MetodoClassificacao.MANUAL,
        justificativa=normalizar_justificativa(justificativa),
        usuario=usuario,
        classificado_em=timezone.now(),
    )
    # Valida choices, tamanhos e as regras de autoria (clean) antes de ir ao banco; os CHECK
    # ficam com o banco (validate_constraints=False evita uma consulta por constraint).
    registro.full_clean(exclude=["lead", "usuario", "sistema", "created_at"], validate_constraints=False)
    with transaction.atomic():
        registro.save()
        transaction.on_commit(
            lambda: registrar(
                "lead_reclassificado", request, usuario, lead_id=lead.pk, classificacao=registro.classificacao
            )
        )
    lead.refresh_from_db(fields=["classificacao_atual", "classificado_em", "updated_at"])
    return registro


def anonimizar_lead(*, lead, usuario, request=None):
    """Atende o pedido de eliminação do titular (LGPD, B15): substitui os dados pessoais,
    exclui logicamente e mantém os históricos."""
    try:
        with transaction.atomic():
            lead.anonimizar()
            transaction.on_commit(lambda: registrar("lead_anonimizado", request, usuario, lead_id=lead.pk))
    except DatabaseError:
        # fn_anonimizar_lead recusa lead inexistente ou já anonimizado.
        raise ValidationError("Este lead não existe ou já foi anonimizado.") from None
    return lead

