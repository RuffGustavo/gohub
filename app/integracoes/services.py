"""Regras de escrita das integrações: única porta de gravação de evento_integracao e
lead_integracao (API interna, tasks, telas e admin).

Saída (aplicação -> n8n): `agendar_notificacao` cria a linha `n8n pendente` na transação do
lead e, depois do commit, enfileira a task; `executar_notificacao` envia o webhook assinado
(sem transação aberta) e grava evento + estado. A retentativa é da fila no banco
(proxima_tentativa_em + beat), única fonte da verdade.

Entrada (n8n -> aplicação): `registrar_evento_recebido` grava evento, integração,
classificação e status numa transação, idempotente por (sistema, chave_idempotencia).

Ordem de locks em todas as escritas: lead -> lead_integracao (o gatilho fn_exigir_lead_ativo
trava o lead). Quem espera por lock trava o lead primeiro (reprocessar_integracao); as consultas
que travam as duas linhas juntas usam skip_locked (nunca esperam, logo não entram em deadlock).

Nunca grave nem registre em log str(exc), URL, cabeçalhos, corpo enviado/recebido ou dados
pessoais: só os códigos de CODIGOS_ERRO (seção 4.7) e ids.
"""

import datetime
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone

from app.leads.models import Lead, LeadClassificacao, LeadStatus, LeadStatusHistorico, Sistema
from app.users.auditoria import registrar

from .assinatura import assinar
from .models import (
    TIPO_EVENTO_NOTIFICACAO,
    WEBHOOK_LEAD_ANONIMIZADO,
    WEBHOOK_LEAD_RECEBIDO,
    WEBHOOK_TESTE,
    EventoIntegracao,
    LeadIntegracao,
    ResultadoEvento,
    StatusIntegracao,
)
from .sanitizacao import sanitizar_texto
from .throttles import teste_conexao_permitido

logger = logging.getLogger("gohub.integracoes")

USER_AGENT = "GoHub-Integracoes/1"
LOTE_REPROCESSAMENTO = 100
TRAVADA_APOS = datetime.timedelta(minutes=10)
BACKOFF_BASE_SEGUNDOS = 60
BACKOFF_TETO_SEGUNDOS = 3600
BACKOFF_JITTER = 0.10
OBSERVACAO_ESGOTADO = "Falha ao notificar o n8n"
# HTTP 4xx que ainda valem nova tentativa automática (decisão P8).
HTTP_TRANSITORIOS = frozenset({408, 425, 429})


class ErroIntegracao(Exception):
    """Base dos erros de negócio das integrações (mensagens fixas, sem dados)."""


class ConflitoIdempotencia(ErroIntegracao):
    """Mesma chave de idempotência já usada para outro lead (I5)."""


class ConflitoIdExterno(ErroIntegracao):
    """id_externo já vinculado a outro lead no mesmo sistema (I7)."""


class LeadIndisponivel(ErroIntegracao):
    """Lead excluído/anonimizado durante a gravação (I14)."""


class ErroGravacao(ErroIntegracao):
    """O banco recusou a gravação por outro motivo (CHECK, tipo...)."""


class LimiteTesteConexao(ErroIntegracao):
    """Testes de conexão demais do mesmo usuário na janela (o envio é síncrono)."""


class NadaAReprocessar(ErroIntegracao):
    """Integração em andamento, com sucesso, de destino ou de lead excluído (P14)."""


# --------------------------------------------------------------------------- utilitários
def _config():
    return settings.INTEGRACOES


def _n8n():
    return settings.INTEGRACOES["n8n"]


def notificacao_configurada():
    """URL do webhook definida (o segredo é obrigatório junto, ver configuracao.py)."""
    return bool(_n8n()["webhook_url"])


def credenciais_configuradas():
    """Presença (nunca o valor) do token e do segredo do n8n, para a tela."""
    n8n = _n8n()
    return bool(n8n["token"] and n8n["segredo_hmac"])


def calcular_backoff(tentativa, aleatorio=random.uniform):
    """60 s x 2^(n-1), com jitter de ±10% e teto de 1 h (decisão P8)."""
    base = BACKOFF_BASE_SEGUNDOS * 2 ** (max(tentativa, 1) - 1)
    segundos = min(base * aleatorio(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER), BACKOFF_TETO_SEGUNDOS)
    return datetime.timedelta(seconds=segundos)


def classificar_excecao(exc):
    """Exceção de `requests` -> (código da seção 4.7, retentável)."""
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls", True
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout", True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "conexao", True
    return "falha_envio", True


def classificar_status(codigo_http):
    """Status HTTP -> (código de erro ou None se 2xx, retentável)."""
    if 200 <= codigo_http < 300:
        return None, False
    if 300 <= codigo_http < 400:
        return f"http_3xx:{codigo_http}", False
    if codigo_http in HTTP_TRANSITORIOS or codigo_http >= 500:
        return f"http_{codigo_http}", True
    if 400 <= codigo_http < 500:
        return f"http_{codigo_http}", False
    return f"http_{codigo_http}", True


def _iso_utc(instante):
    return instante.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _corpo_json(dados):
    """JSON compacto e com chaves ordenadas (o n8n valida a assinatura sobre estes bytes)."""
    return json.dumps(dados, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()


def entrega_id(lead_id, evento):
    """Estável entre tentativas, para o n8n deduplicar a entrega."""
    return str(uuid.uuid5(uuid.UUID(str(lead_id)), evento))


@dataclass(frozen=True)
class ResultadoEnvio:
    ok: bool
    codigo_http: int | None
    codigo_erro: str | None
    retentavel: bool
    duracao_ms: int


def _enviar(dados, *, evento, entrega=None, lead_id=None):
    """POST assinado ao webhook do n8n. Nunca chamado com transação aberta.

    Corpo da resposta descartado (stream + close): só o status interessa.
    """
    cfg = _config()
    n8n = cfg["n8n"]
    corpo = _corpo_json(dados)
    timestamp = str(int(time.time()))
    cabecalhos = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GoHub-Evento": evento,
        "X-GoHub-Timestamp": timestamp,
        "X-GoHub-Assinatura": assinar(corpo, timestamp, n8n["segredo_hmac"]),
    }
    if entrega:
        cabecalhos["X-GoHub-Entrega"] = entrega
    inicio = time.monotonic()
    try:
        resposta = requests.post(
            n8n["webhook_url"],
            data=corpo,
            headers=cabecalhos,
            timeout=(cfg["timeout_conexao"], cfg["timeout_leitura"]),
            allow_redirects=False,
            stream=True,
        )
        codigo_http = resposta.status_code
        resposta.close()
    except requests.RequestException as exc:
        codigo, retentavel = classificar_excecao(exc)
        duracao = int((time.monotonic() - inicio) * 1000)
        if codigo == "tls":
            logger.warning("evento=webhook_tls_falhou lead_id=%s sistema=n8n", lead_id or "-")
        elif codigo == "falha_envio":
            logger.warning(
                "evento=webhook_falha_inesperada lead_id=%s sistema=n8n tipo=%s", lead_id or "-", exc.__class__.__name__
            )
        return ResultadoEnvio(False, None, codigo, retentavel, duracao)
    duracao = int((time.monotonic() - inicio) * 1000)
    codigo, retentavel = classificar_status(codigo_http)
    return ResultadoEnvio(codigo is None, codigo_http, codigo, retentavel, duracao)


def _disparar(task, *args):
    """Enfileira a task sem nunca quebrar quem chamou (broker fora: a fila no banco recupera)."""
    try:
        task.apply_async(args=[str(arg) for arg in args], retry=False)
    except Exception as exc:  # broker indisponível, serialização...: só registra
        logger.warning("evento=fila_indisponivel tarefa=%s tipo=%s", task.name, exc.__class__.__name__)


# --------------------------------------------------------------------------- saída
_SQL_AGENDAR = """
    INSERT INTO lead_integracao (lead_id, sistema_id, status, tentativas, proxima_tentativa_em)
    SELECT %s, id, %s, 0, %s FROM sistema WHERE codigo = %s
    ON CONFLICT (lead_id, sistema_id) DO NOTHING
"""


def agendar_notificacao(lead_id):
    """I1/I2: chamado DENTRO da transação do INSERT do lead novo.

    Cria `lead_integracao (n8n, pendente)` numa única consulta e, depois do commit, enfileira
    o envio. Falha do broker ou URL não configurada deixam a linha pendente (o beat recupera).
    """
    with connection.cursor() as cursor:
        cursor.execute(_SQL_AGENDAR, [lead_id, StatusIntegracao.PENDENTE, timezone.now(), Sistema.N8N])
    transaction.on_commit(lambda: _enfileirar_notificacao(lead_id))


def _enfileirar_notificacao(lead_id):
    if not notificacao_configurada():
        return
    from .tasks import notificar_lead_recebido  # import tardio: tasks importa este módulo

    _disparar(notificar_lead_recebido, lead_id)


def _carregar_para_envio(lead_id):
    """Linha n8n de lead ativo, travada junto com o lead; None se outra execução a segura."""
    return (
        LeadIntegracao.objects.select_for_update(skip_locked=True, no_key=True, of=("self", "lead"))
        .select_related("lead", "sistema")
        .only(
            "id", "status", "tentativas", "proxima_tentativa_em", "lead", "sistema", "sistema__ativo",
            "sistema__codigo", "lead__created_at", "lead__status_atual", "lead__deleted_at",
        )
        .filter(lead_id=lead_id, sistema__codigo=Sistema.N8N, lead__deleted_at__isnull=True)
        .first()
    )


def executar_notificacao(lead_id):
    """Corpo da task notificar_lead_recebido. Retorna o ResultadoEnvio ou None se nada foi feito.

    Sai sem efeito (e sem consumir tentativa) se a linha estiver travada por outra execução,
    não estiver vencida na fila, já tiver sucesso/estiver em andamento, se o lead não estiver
    ativo ou se o sistema estiver inativo / a URL não estiver configurada (I16, I17).
    """
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except ValueError:
        logger.warning("evento=notificacao_id_invalido")
        return None

    agora = timezone.now()
    with transaction.atomic():
        integracao = _carregar_para_envio(lead_uuid)
        if (
            integracao is None
            or integracao.status not in (StatusIntegracao.PENDENTE, StatusIntegracao.ERRO)
            or integracao.proxima_tentativa_em is None
            or integracao.proxima_tentativa_em > agora
            or not integracao.sistema.ativo
            or not notificacao_configurada()
        ):
            return None
        LeadIntegracao.objects.filter(pk=integracao.pk).update(status=StatusIntegracao.EM_ANDAMENTO)
    # Commit feito: o HTTP nunca acontece com transação aberta nem lock mantido.

    tentativa = integracao.tentativas + 1
    dados = {
        "evento": WEBHOOK_LEAD_RECEBIDO,
        "lead_id": str(lead_uuid),
        "ocorrido_em": _iso_utc(integracao.lead.created_at),
        "entrega_id": entrega_id(lead_uuid, WEBHOOK_LEAD_RECEBIDO),
    }
    try:
        resultado = _enviar(dados, evento=WEBHOOK_LEAD_RECEBIDO, entrega=dados["entrega_id"], lead_id=lead_uuid)
    except Exception:
        # Erro inesperado (não de rede): grava o estado de erro, registra e relança para o worker.
        resultado = ResultadoEnvio(False, None, "falha_envio", True, 0)
        _gravar_tentativa(integracao, tentativa, resultado)
        logger.exception("evento=notificacao_erro_inesperado lead_id=%s sistema=n8n tentativa=%s", lead_uuid, tentativa)
        raise
    _gravar_tentativa(integracao, tentativa, resultado)
    return resultado


def _gravar_tentativa(integracao, tentativa, resultado):
    """Evento de saída + novo estado da linha numa transação (seção 4.6, passo 5)."""
    agora = timezone.now()
    lead_id = integracao.lead_id
    maximo = _config()["max_tentativas"]
    campos = {"tentativas": F("tentativas") + 1, "ultima_tentativa_em": agora}
    encerrou = False
    if resultado.ok:
        campos.update(
            status=StatusIntegracao.SUCESSO, sucesso_em=agora, ultimo_erro=None, proxima_tentativa_em=None
        )
    else:
        campos.update(status=StatusIntegracao.ERRO, ultimo_erro=resultado.codigo_erro)
        if resultado.retentavel and tentativa < maximo:
            campos["proxima_tentativa_em"] = agora + calcular_backoff(tentativa)
        else:
            campos["proxima_tentativa_em"] = None
            encerrou = True
    try:
        with transaction.atomic():
            evento = EventoIntegracao(
                lead_id=lead_id,
                sistema_id=integracao.sistema_id,
                tipo_evento=TIPO_EVENTO_NOTIFICACAO,
                resultado=ResultadoEvento.SUCESSO if resultado.ok else ResultadoEvento.ERRO,
                chave_idempotencia=f"saida:{uuid.uuid4()}",
                mensagem_erro=resultado.codigo_erro,
                payload={
                    "tentativa": tentativa,
                    "codigo_http": resultado.codigo_http,
                    "duracao_ms": resultado.duracao_ms,
                },
                ocorrido_em=agora,
            )
            evento.save()
            LeadIntegracao.objects.filter(pk=integracao.pk).update(**campos)
            # P9: ao encerrar as tentativas automáticas, um único histórico "erro" (não repete se
            # o lead já está em erro, ex.: cliques de reprocessamento acima do limite).
            if encerrou and integracao.lead.status_atual != LeadStatus.ERRO:
                LeadStatusHistorico(
                    lead_id=lead_id,
                    status_novo=LeadStatus.ERRO,
                    sistema=Sistema.objects.only("id").get(codigo=Sistema.APLICACAO_WEB),
                    evento=evento,
                    observacao=OBSERVACAO_ESGOTADO,
                    ocorrido_em=agora,
                ).save()
    except IntegrityError:
        # Lead anonimizado durante o envio (gatilho fn_exigir_lead_ativo): nada a registrar.
        logger.warning("evento=notificacao_lead_indisponivel lead_id=%s sistema=n8n", lead_id)
        return
    logger.info(
        "evento=notificacao_enviada lead_id=%s sistema=n8n tentativa=%s codigo=%s duracao_ms=%s",
        lead_id, tentativa, resultado.codigo_erro or "ok", resultado.duracao_ms,
    )
    if encerrou:
        logger.warning(
            "evento=notificacao_encerrada lead_id=%s sistema=n8n tentativa=%s codigo=%s",
            lead_id, tentativa, resultado.codigo_erro,
        )


def leads_para_reprocessar(limite=LOTE_REPROCESSAMENTO):
    """Ids de leads com a notificação vencida na fila (índice ix_lead_integracao_fila)."""
    if not notificacao_configurada():
        return []
    n8n = Sistema.objects.only("id", "ativo").filter(codigo=Sistema.N8N).first()
    if n8n is None or not n8n.ativo:
        return []
    return list(
        LeadIntegracao.objects.filter(
            sistema_id=n8n.pk,
            status__in=[StatusIntegracao.PENDENTE, StatusIntegracao.ERRO],
            proxima_tentativa_em__lte=timezone.now(),
            lead__deleted_at__isnull=True,
        )
        .order_by("proxima_tentativa_em")
        .values_list("lead_id", flat=True)[:limite]
    )


def reprocessar_pendentes(limite=LOTE_REPROCESSAMENTO):
    """Corpo da task periódica: enfileira o envio de cada lead vencido (só ids)."""
    from .tasks import notificar_lead_recebido

    ids = leads_para_reprocessar(limite)
    for lead_id in ids:
        _disparar(notificar_lead_recebido, lead_id)
    if ids:
        logger.info("evento=reprocessamento_enfileirado quantidade=%s", len(ids))
    return len(ids)


def liberar_travadas(agora=None):
    """I18: em_andamento há mais de 10 min (worker morreu) volta para a fila."""
    agora = agora or timezone.now()
    with transaction.atomic():
        travadas = list(
            LeadIntegracao.objects.select_for_update(skip_locked=True, no_key=True, of=("self", "lead"))
            .select_related("lead")
            .only("id", "lead", "lead__deleted_at")
            .filter(
                status=StatusIntegracao.EM_ANDAMENTO,
                updated_at__lt=agora - TRAVADA_APOS,
                lead__deleted_at__isnull=True,
            )[:500]
        )
        if not travadas:
            return 0
        total = LeadIntegracao.objects.filter(pk__in=[item.pk for item in travadas]).update(
            status=StatusIntegracao.ERRO, ultimo_erro="interrompida", proxima_tentativa_em=agora
        )
    logger.warning("evento=tentativas_liberadas quantidade=%s", total)
    return total


def reprocessar_integracao(*, integracao, usuario, request=None):
    """Reprocessamento manual (decisão P14): só a linha n8n de lead ativo em pendente/erro.

    Não zera tentativas; permite passar do limite automático (uma tentativa por pedido).
    Levanta NadaAReprocessar nos demais casos.
    """
    with transaction.atomic():
        # Mesma ordem de locks das demais escritas: primeiro o lead, depois lead_integracao
        # (um SELECT com JOIN travaria lead_integracao antes e poderia gerar deadlock).
        lead_id = LeadIntegracao.objects.filter(pk=integracao.pk).values_list("lead_id", flat=True).first()
        lead_travado = (
            Lead.objects.select_for_update(no_key=True)
            .filter(pk=lead_id, deleted_at__isnull=True)
            .values_list("id", flat=True)
            .first()
            if lead_id is not None
            else None
        )
        atual = (
            LeadIntegracao.objects.select_for_update(no_key=True, of=("self",))
            .select_related("sistema")
            .filter(pk=integracao.pk)
            .first()
            if lead_travado is not None
            else None
        )
        if (
            atual is None
            or atual.sistema.codigo != Sistema.N8N
            or atual.status not in (StatusIntegracao.PENDENTE, StatusIntegracao.ERRO)
        ):
            raise NadaAReprocessar("Nada a reprocessar.")
        LeadIntegracao.objects.filter(pk=atual.pk).update(proxima_tentativa_em=timezone.now())
        lead_id = atual.lead_id
        transaction.on_commit(lambda: _enfileirar_notificacao(lead_id))
        transaction.on_commit(
            lambda: registrar(
                "integracao_reprocessada", request, usuario, integracao_id=atual.pk, lead_id=lead_id, sistema=Sistema.N8N
            )
        )
    atual.refresh_from_db()
    return atual


def alternar_sistema(*, sistema, usuario, request=None):
    """Ativa/desativa um sistema. aplicacao_web nunca é desativado (autor dos gatilhos do lead)."""
    with transaction.atomic():
        atual = Sistema.objects.select_for_update().get(pk=sistema.pk)
        if atual.codigo == Sistema.APLICACAO_WEB:
            raise ValidationError("A aplicação web não pode ser desativada.")
        atual.ativo = not atual.ativo
        atual.save(update_fields=["ativo"])
        evento = "sistema_ativado" if atual.ativo else "sistema_desativado"
        transaction.on_commit(lambda: registrar(evento, request, usuario, sistema=atual.codigo))
    return atual


def testar_conexao(*, sistema, usuario, request=None):
    """Envia `integracao.teste` assinado, de forma síncrona. Não grava evento (não há lead).

    Retorna (ok, código): código da seção 4.7, "ok" ou "nao_configurado".
    """
    if sistema.codigo != Sistema.N8N:
        raise ValidationError("Só o n8n tem teste de conexão.")
    if not teste_conexao_permitido(usuario):
        raise LimiteTesteConexao("Aguarde um minuto para testar de novo.")
    if not notificacao_configurada():
        ok, codigo = False, "nao_configurado"
    else:
        resultado = _enviar({"evento": WEBHOOK_TESTE, "ocorrido_em": _iso_utc(timezone.now())}, evento=WEBHOOK_TESTE)
        ok, codigo = resultado.ok, resultado.codigo_erro or "ok"
    registrar("integracao_teste_conexao", request, usuario, sistema=sistema.codigo, resultado=codigo)
    return ok, codigo


# --------------------------------------------------------------------------- anonimização (P13)
def agendar_aviso_anonimizacao(lead_id):
    """Chamado no on_commit de anonimizar_lead: avisa o n8n para eliminar o lead no CRM."""
    if not notificacao_configurada():
        logger.warning("evento=anonimizacao_sem_aviso lead_id=%s motivo=nao_configurado", lead_id)
        return
    from .tasks import notificar_lead_anonimizado

    _disparar(notificar_lead_anonimizado, lead_id)


def executar_aviso_anonimizacao(lead_id):
    """Envia `lead.anonimizado` com o lead_id e o id_externo de cada destino.

    O lead já está excluído: os gatilhos impedem gravar evento ou estado, então o resultado
    fica só no log e a retentativa é da própria task (Celery). Retorna o ResultadoEnvio.
    """
    lead_uuid = uuid.UUID(str(lead_id))
    lead = Lead.objects.only("id", "anonimizado_em").filter(pk=lead_uuid, anonimizado_em__isnull=False).first()
    if lead is None or not notificacao_configurada():
        return None
    n8n = Sistema.objects.only("ativo").filter(codigo=Sistema.N8N).first()
    if n8n is None or not n8n.ativo:
        logger.warning("evento=anonimizacao_sem_aviso lead_id=%s motivo=sistema_inativo", lead_uuid)
        return None
    ids_externos = dict(
        LeadIntegracao.objects.filter(lead_id=lead_uuid, id_externo__isnull=False)
        .order_by("sistema__codigo")
        .values_list("sistema__codigo", "id_externo")
    )
    dados = {
        "evento": WEBHOOK_LEAD_ANONIMIZADO,
        "lead_id": str(lead_uuid),
        "ocorrido_em": _iso_utc(lead.anonimizado_em),
        "entrega_id": entrega_id(lead_uuid, WEBHOOK_LEAD_ANONIMIZADO),
        "ids_externos": ids_externos,
    }
    resultado = _enviar(dados, evento=WEBHOOK_LEAD_ANONIMIZADO, entrega=dados["entrega_id"], lead_id=lead_uuid)
    logger.info(
        "evento=anonimizacao_avisada lead_id=%s sistema=n8n codigo=%s duracao_ms=%s",
        lead_uuid, resultado.codigo_erro or "ok", resultado.duracao_ms,
    )
    return resultado


# --------------------------------------------------------------------------- entrada
_SQL_INSERIR_EVENTO = """
    INSERT INTO evento_integracao
        (lead_id, sistema_id, tipo_evento, resultado, chave_idempotencia, id_externo,
         mensagem_erro, payload, ocorrido_em)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
    ON CONFLICT (sistema_id, chave_idempotencia) DO NOTHING
    RETURNING id, recebido_em
"""

_SQL_GARANTIR_INTEGRACAO = """
    INSERT INTO lead_integracao (lead_id, sistema_id, status, tentativas)
    VALUES (%s, %s, %s, 0)
    ON CONFLICT (lead_id, sistema_id) DO NOTHING
"""


def _restricao(exc):
    causa = exc.__cause__
    diag = getattr(causa, "diag", None)
    return getattr(diag, "constraint_name", None), getattr(causa, "pgcode", None)


def registrar_evento_recebido(*, sistema, lead, dados):
    """Grava o retorno de um sistema numa única transação (A9, I4-I15). Retorna (evento, criado).

    `dados` é o validated_data do EventoEntradaSerializer (já sanitizado e validado).
    Levanta ConflitoIdempotencia, ConflitoIdExterno, LeadIndisponivel ou ErroGravacao.
    """
    ocorrido_em = dados["ocorrido_em"]
    resultado = dados["resultado"]
    destino = dados.get("sistema_destino")
    payload = dados.get("payload")
    valores = [
        lead.pk, sistema.pk, dados["tipo_evento"], resultado, dados["chave_idempotencia"],
        dados.get("id_externo"), dados.get("mensagem_erro"),
        None if payload is None else json.dumps(payload), ocorrido_em,
    ]
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(_SQL_INSERIR_EVENTO, valores)
                linha = cursor.fetchone()
            if linha is None:
                existente = EventoIntegracao.objects.select_related("sistema").get(
                    sistema=sistema, chave_idempotencia=dados["chave_idempotencia"]
                )
                if existente.lead_id != lead.pk:
                    raise ConflitoIdempotencia("Chave de idempotência já usada para outro lead.")
                return existente, False

            evento = EventoIntegracao(
                id=linha[0], lead=lead, sistema=sistema, tipo_evento=dados["tipo_evento"], resultado=resultado,
                chave_idempotencia=dados["chave_idempotencia"], id_externo=dados.get("id_externo"),
                mensagem_erro=dados.get("mensagem_erro"), payload=payload, ocorrido_em=ocorrido_em,
                recebido_em=linha[1],
            )
            evento._state.adding = False
            evento._state.db = connection.alias

            if destino is not None and resultado != ResultadoEvento.IGNORADO:
                _atualizar_destino(lead, destino, evento)

            classificacao = dados.get("classificacao")
            if classificacao:
                LeadClassificacao(
                    lead=lead,
                    classificacao=classificacao["classificacao"],
                    metodo=classificacao["metodo"],
                    justificativa=classificacao.get("justificativa"),
                    versao_criterio=classificacao.get("versao_criterio"),
                    confianca=classificacao.get("confianca"),
                    sistema=sistema,
                    evento=evento,
                    classificado_em=ocorrido_em,
                ).save()

            if dados.get("status_novo"):
                LeadStatusHistorico(
                    lead=lead,
                    status_novo=dados["status_novo"],
                    sistema=sistema,
                    evento=evento,
                    observacao=dados.get("observacao"),
                    ocorrido_em=ocorrido_em,
                ).save()
    except IntegrityError as exc:
        restricao, pgcode = _restricao(exc)
        if restricao == "uq_lead_integracao_id_externo":
            logger.warning("evento=id_externo_em_uso lead_id=%s sistema=%s", lead.pk, sistema.codigo)
            raise ConflitoIdExterno("ID externo já vinculado a outro lead.") from None
        if pgcode == "23503":  # fn_exigir_lead_ativo: lead excluído no meio da gravação
            raise LeadIndisponivel("Lead indisponível.") from None
        logger.warning("evento=evento_recusado lead_id=%s sistema=%s restricao=%s", lead.pk, sistema.codigo, restricao)
        raise ErroGravacao("Não foi possível registrar o evento.") from None
    except DataError:
        raise ErroGravacao("Não foi possível registrar o evento.") from None
    return evento, True


def _atualizar_destino(lead, destino, evento):
    """Upsert de lead_integracao do sistema de destino com a linha travada (I15)."""
    with connection.cursor() as cursor:
        cursor.execute(_SQL_GARANTIR_INTEGRACAO, [lead.pk, destino.pk, StatusIntegracao.PENDENTE])
    integracao = (
        LeadIntegracao.objects.select_for_update(no_key=True)
        .only("id", "status", "sucesso_em")
        .get(lead=lead, sistema=destino)
    )
    # I15: evento anterior ao sucesso registrado não altera o estado (o evento continua gravado).
    if integracao.sucesso_em is not None and evento.ocorrido_em < integracao.sucesso_em:
        return
    campos = {"tentativas": F("tentativas") + 1, "ultima_tentativa_em": evento.ocorrido_em}
    if evento.resultado == ResultadoEvento.SUCESSO:
        campos.update(
            status=StatusIntegracao.SUCESSO, sucesso_em=evento.ocorrido_em, ultimo_erro=None, proxima_tentativa_em=None
        )
    else:
        campos.update(status=StatusIntegracao.ERRO, ultimo_erro=sanitizar_texto(evento.mensagem_erro))
    if evento.id_externo:
        campos["id_externo"] = evento.id_externo
    LeadIntegracao.objects.filter(pk=integracao.pk).update(**campos)
