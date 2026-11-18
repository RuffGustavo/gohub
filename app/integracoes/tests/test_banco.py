"""Banco: CHECK, unicidades, FKs, gatilhos de imutabilidade e de lead ativo, anonimização,
reverso das migrations, commits reais (on_commit) e concorrência."""

import threading
import time
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from app.leads.models import Lead, LeadStatus, LeadStatusHistorico, Sistema
from app.leads.tests.base import CAPTURA_VALIDA, URL_CAPTURA

from .. import services
from ..models import EventoIntegracao, LeadIntegracao, ResultadoEvento, StatusIntegracao
from .base import CONFIG_TESTE, anonimizar, integracao_n8n, novo_lead, patch_post, resposta_http, sistema

SQL_EVENTO = (
    "INSERT INTO evento_integracao (lead_id, sistema_id, tipo_evento, resultado, chave_idempotencia, "
    "mensagem_erro, payload, ocorrido_em) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())"
)


def executar(sql, params=()):
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(sql, params)


@override_settings(**CONFIG_TESTE)
class RestricoesTests(TestCase):
    def setUp(self):
        self.lead = novo_lead()
        self.n8n = sistema(Sistema.N8N)
        self.crm = sistema(Sistema.CRM)

    def evento_sql(self, tipo="crm.sincronizacao", resultado="sucesso", chave="exec-001:no", mensagem=None,
                   payload=None, lead=None):
        executar(SQL_EVENTO, [(lead or self.lead).pk, self.n8n.pk, tipo, resultado, chave, mensagem,
                              None if payload is None else payload])

    def test_checks_do_evento(self):
        for kwargs in (
            {"resultado": "erro"},  # erro sem mensagem
            {"tipo": "CRM Sync"},
            {"tipo": ".crm"},
            {"chave": "curta"},
            {"chave": "com espaço 123"},
            {"payload": "[1, 2]"},
            {"payload": '"texto"'},
            {"resultado": "talvez"},
        ):
            with self.subTest(**kwargs), self.assertRaises(IntegrityError):
                self.evento_sql(**kwargs)
        self.evento_sql(payload='{"tentativa": 1}')
        with self.assertRaises(IntegrityError):  # uq_evento_idempotencia
            self.evento_sql(payload='{"tentativa": 2}')

    def test_checks_da_integracao(self):
        base = "INSERT INTO lead_integracao (lead_id, sistema_id, status, tentativas, ultima_tentativa_em, sucesso_em) VALUES (%s, %s, %s, %s, %s, %s)"
        agora = timezone.now()
        for status, tentativas, ultima, sucesso in (
            ("sucesso", 0, None, None),  # sucesso sem data
            ("erro", 1, None, None),  # tentativas sem data
            ("pendente", -1, None, None),
            ("cancelado", 0, None, None),
        ):
            with self.subTest(status=status, tentativas=tentativas), self.assertRaises(IntegrityError):
                executar(base, [self.lead.pk, self.crm.pk, status, tentativas, ultima, sucesso])
        executar(base, [self.lead.pk, self.crm.pk, "sucesso", 1, agora, agora])
        with self.assertRaises(IntegrityError):  # uq_lead_integracao_lead_sistema
            executar(base, [self.lead.pk, self.crm.pk, "pendente", 0, None, None])

    def test_id_externo_unico_por_sistema(self):
        outro = novo_lead(email="outro@empresa.com")
        LeadIntegracao.objects.create(lead=self.lead, sistema=self.crm, id_externo="CRM-1")
        LeadIntegracao.objects.create(lead=outro, sistema=sistema(Sistema.NOTIFICACAO), id_externo="CRM-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            LeadIntegracao.objects.create(lead=outro, sistema=self.crm, id_externo="CRM-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            LeadIntegracao.objects.create(lead=outro, sistema=self.crm, id_externo="CRM 1")

    def test_evento_imutavel_no_banco(self):
        self.evento_sql(payload='{"tentativa": 1}')
        for sql in ("UPDATE evento_integracao SET resultado = 'erro', mensagem_erro = 'x'",
                    "UPDATE evento_integracao SET payload = NULL",
                    "DELETE FROM evento_integracao"):
            with self.subTest(sql=sql), self.assertRaises(DatabaseError) as ctx:
                executar(sql)
            self.assertEqual(ctx.exception.__cause__.pgcode, "42501")  # insufficient_privilege

    def test_evento_imutavel_no_python(self):
        self.evento_sql()
        evento = EventoIntegracao.objects.get()
        with self.assertRaises(ValidationError):
            evento.save()
        with self.assertRaises(ValidationError):
            evento.delete()
        with self.assertRaises(ValidationError):
            EventoIntegracao.objects.update(resultado="erro")
        with self.assertRaises(ValidationError):
            EventoIntegracao.objects.all().delete()

    def test_nada_se_vincula_a_lead_excluido(self):
        LeadIntegracao.objects.create(lead=self.lead, sistema=self.crm)
        anonimizar(self.lead)
        with self.assertRaises(IntegrityError):
            self.evento_sql()
        with self.assertRaises(IntegrityError):
            executar("INSERT INTO lead_integracao (lead_id, sistema_id) VALUES (%s, %s)",
                     [self.lead.pk, sistema(Sistema.NOTIFICACAO).pk])
        with self.assertRaises(IntegrityError):
            executar("UPDATE lead_integracao SET status = 'erro' WHERE lead_id = %s", [self.lead.pk])

    def test_flag_de_anonimizacao_nao_libera_outras_escritas(self):
        """M1: com gohub.anonimizando ligado, só a limpeza de ultimo_erro passa no lead excluído."""
        LeadIntegracao.objects.create(lead=self.lead, sistema=self.crm, status=StatusIntegracao.ERRO, tentativas=1,
                                      ultima_tentativa_em=timezone.now(), ultimo_erro="CRM 503")
        anonimizar(self.lead)
        for sql, params in (
            ("UPDATE lead_integracao SET status = 'pendente' WHERE lead_id = %s", [self.lead.pk]),
            ("UPDATE lead_integracao SET id_externo = 'X-1' WHERE lead_id = %s", [self.lead.pk]),
            ("INSERT INTO lead_integracao (lead_id, sistema_id) VALUES (%s, %s)",
             [self.lead.pk, sistema(Sistema.NOTIFICACAO).pk]),
        ):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('gohub.anonimizando', 'on', true)")
                    cursor.execute(sql, params)

    def test_fks_com_nome_e_restrict_do_ddl(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname, confdeltype FROM pg_constraint WHERE contype = 'f' AND conname = ANY(%s)",
                [["fk_evento_lead", "fk_evento_sistema", "fk_lead_integracao_lead", "fk_lead_integracao_sistema",
                  "fk_classificacao_evento", "fk_status_hist_evento"]],
            )
            linhas = dict(cursor.fetchall())
        self.assertEqual(len(linhas), 6)
        self.assertEqual(set(linhas.values()), {"r"})
        self.evento_sql()
        with self.assertRaises(IntegrityError):
            executar("DELETE FROM sistema WHERE id = %s", [self.n8n.pk])

    def test_anonimizacao_limpa_eventos_e_integracoes(self):
        self.evento_sql(chave="exec-001:no", resultado="erro", mensagem="CRM 503 ana@empresa.com",
                        payload='{"execucao_id": "1"}')
        self.evento_sql(chave="exec-002:no", resultado="sucesso", payload='{"tentativa": 1}')
        evento = EventoIntegracao.objects.get(chave_idempotencia="exec-001:no")
        LeadStatusHistorico.objects.create(lead=self.lead, status_novo=LeadStatus.ERRO, sistema=self.n8n,
                                           evento=evento, observacao="Falha")
        LeadIntegracao.objects.create(lead=self.lead, sistema=self.crm, status=StatusIntegracao.ERRO, tentativas=1,
                                      ultima_tentativa_em=timezone.now(), ultimo_erro="CRM 503", id_externo="CRM-9")
        antes = {e.pk: (e.tipo_evento, e.resultado, e.chave_idempotencia, e.ocorrido_em, e.recebido_em)
                 for e in EventoIntegracao.objects.all()}
        anonimizar(self.lead)
        for evento in EventoIntegracao.objects.all():
            self.assertIsNone(evento.payload)
            self.assertEqual(antes[evento.pk],
                             (evento.tipo_evento, evento.resultado, evento.chave_idempotencia, evento.ocorrido_em,
                              evento.recebido_em))
        self.assertEqual(EventoIntegracao.objects.get(chave_idempotencia="exec-001:no").mensagem_erro, "[removido]")
        self.assertIsNone(EventoIntegracao.objects.get(chave_idempotencia="exec-002:no").mensagem_erro)
        crm = LeadIntegracao.objects.get(sistema=self.crm)
        self.assertIsNone(crm.ultimo_erro)
        self.assertEqual(crm.id_externo, "CRM-9")  # preservado para o aviso ao n8n (risco LGPD registrado)
        historico = LeadStatusHistorico.objects.get(evento=evento)
        self.assertIsNone(historico.observacao)
        self.assertEqual(historico.status_novo, LeadStatus.ERRO)

    def test_anonimizacao_nao_libera_outras_alteracoes(self):
        self.evento_sql(payload='{"tentativa": 1}')
        anonimizar(self.lead)
        with self.assertRaises(DatabaseError):
            executar("UPDATE evento_integracao SET tipo_evento = 'outro.tipo'")
        with self.assertRaises(DatabaseError):  # sem a flag de anonimização, nem a limpeza é aceita
            executar("UPDATE evento_integracao SET payload = NULL")


class MigradoMixin:
    """Garante o banco de teste no estado final (outro teste pode ter revertido migrations)."""

    serialized_rollback = True  # restaura os sistemas iniciais apagados pelo flush

    def setUp(self):
        call_command("migrate", verbosity=0)
        super().setUp()


class ReversoDasMigrationsTests(MigradoMixin, TransactionTestCase):
    def funcoes(self, *nomes):
        with connection.cursor() as cursor:
            cursor.execute("SELECT proname FROM pg_proc WHERE proname = ANY(%s)", [list(nomes)])
            return {linha[0] for linha in cursor.fetchall()}

    def colunas(self, tabela):
        with connection.cursor() as cursor:
            return {coluna.name for coluna in connection.introspection.get_table_description(cursor, tabela)}

    def test_reverter_e_reaplicar(self):
        call_command("migrate", "integracoes", "zero", verbosity=0)
        with connection.cursor() as cursor:
            tabelas = set(connection.introspection.table_names(cursor))
            cursor.execute("SELECT prosrc FROM pg_proc WHERE proname = 'fn_anonimizar_lead'")
            corpo = cursor.fetchone()[0]
        self.assertFalse({"evento_integracao", "lead_integracao"} & tabelas)
        self.assertNotIn("evento_id", self.colunas("lead_status_historico"))
        self.assertNotIn("evento_id", self.colunas("lead_classificacao"))
        self.assertEqual(self.funcoes("fn_evento_imutavel"), set())
        self.assertEqual(self.funcoes("fn_exigir_lead_ativo", "fn_set_updated_at"),
                         {"fn_exigir_lead_ativo", "fn_set_updated_at"})
        self.assertNotIn("evento_integracao", corpo)
        # A anonimização original continua funcionando sem as tabelas de integração.
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao) "
                "VALUES ('Ana', 'reverso@empresa.com', '+5511987654321', 'Acme', 'CEO', now(), '1.0') RETURNING id"
            )
            lead_id = cursor.fetchone()[0]
            cursor.execute("SELECT fn_anonimizar_lead(%s)", [lead_id])
            cursor.execute("SELECT anonimizado_em IS NOT NULL FROM lead WHERE id = %s", [lead_id])
            self.assertTrue(cursor.fetchone()[0])

        call_command("migrate", verbosity=0)
        self.assertEqual(self.funcoes("fn_evento_imutavel"), {"fn_evento_imutavel"})
        self.assertIn("evento_id", self.colunas("lead_status_historico"))
        with override_settings(**CONFIG_TESTE):
            lead = novo_lead(email="depois@empresa.com")
        self.assertEqual(integracao_n8n(lead).status, StatusIntegracao.PENDENTE)


@override_settings(**CONFIG_TESTE)
class CommitRealTests(MigradoMixin, TransactionTestCase):
    def test_webhook_so_depois_do_commit(self):
        visto = {}

        def ao_enfileirar(args, retry):
            # Na hora de enfileirar, a linha já está gravada e visível em outra conexão.
            visto["lead_id"] = args[0]

        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async", side_effect=ao_enfileirar):
            with transaction.atomic():
                lead = novo_lead()
                self.assertEqual(visto, {})
        self.assertEqual(visto["lead_id"], str(lead.pk))
        self.assertEqual(integracao_n8n(lead).status, StatusIntegracao.PENDENTE)

    def test_broker_fora_nao_altera_a_resposta_publica(self):
        from rest_framework.test import APIClient

        cliente = APIClient()
        with mock.patch("app.integracoes.tasks.notificar_lead_recebido.apply_async",
                        side_effect=OperationalError("fora")):
            novo = cliente.post(URL_CAPTURA, CAPTURA_VALIDA, format="json")
            duplicado = cliente.post(URL_CAPTURA, CAPTURA_VALIDA, format="json")
        self.assertEqual((novo.status_code, novo.content), (duplicado.status_code, duplicado.content))
        lead = Lead.objects.get()
        self.assertEqual(integracao_n8n(lead).status, StatusIntegracao.PENDENTE)
        self.assertEqual(services.leads_para_reprocessar(), [lead.pk])


@override_settings(**CONFIG_TESTE)
class ConcorrenciaTests(MigradoMixin, TransactionTestCase):
    def em_paralelo(self, funcao, argumentos):
        barreira = threading.Barrier(len(argumentos))
        erros = []

        def executar_thread(argumento):
            try:
                barreira.wait()
                funcao(argumento)
            except Exception as exc:  # verificado na thread principal
                erros.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=executar_thread, args=(a,)) for a in argumentos]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return erros

    def dados(self, chave, **extra):
        base = {"chave_idempotencia": chave, "tipo_evento": "crm.sincronizacao", "ocorrido_em": timezone.now(),
                "resultado": ResultadoEvento.ERRO, "mensagem_erro": "CRM 503", "sistema_destino": sistema(Sistema.CRM),
                "status_novo": LeadStatus.ERRO}
        base.update(extra)
        return base

    def test_mesma_chave_simultanea_um_efeito(self):
        lead = novo_lead()
        n8n = sistema(Sistema.N8N)
        criados = []
        erros = self.em_paralelo(
            lambda _: criados.append(
                services.registrar_evento_recebido(sistema=n8n, lead=lead, dados=self.dados("exec-777:no"))[1]
            ),
            range(8),
        )
        self.assertEqual(erros, [])
        self.assertEqual(sorted(criados), [False] * 7 + [True])
        self.assertEqual(EventoIntegracao.objects.count(), 1)
        self.assertEqual(LeadStatusHistorico.objects.filter(lead=lead, status_novo=LeadStatus.ERRO).count(), 1)
        self.assertEqual(LeadIntegracao.objects.get(lead=lead, sistema__codigo="crm").tentativas, 1)

    def test_eventos_diferentes_simultaneos_contam_todas_as_tentativas(self):
        lead = novo_lead()
        n8n = sistema(Sistema.N8N)
        erros = self.em_paralelo(
            lambda i: services.registrar_evento_recebido(
                sistema=n8n, lead=lead, dados=self.dados(f"exec-{i:03d}:no", status_novo=None)
            ),
            range(8),
        )
        self.assertEqual(erros, [])
        self.assertEqual(LeadIntegracao.objects.get(lead=lead, sistema__codigo="crm").tentativas, 8)

    def test_duas_execucoes_da_task_enviam_uma_vez(self):
        lead = novo_lead()

        def lento(*args, **kwargs):
            time.sleep(0.3)
            return resposta_http(200)

        with patch_post(side_effect=lento) as post:
            erros = self.em_paralelo(lambda _: services.executar_notificacao(str(lead.pk)), range(4))
        self.assertEqual(erros, [])
        self.assertEqual(post.call_count, 1)
        integracao = integracao_n8n(lead)
        self.assertEqual((integracao.status, integracao.tentativas), (StatusIntegracao.SUCESSO, 1))
        self.assertEqual(EventoIntegracao.objects.count(), 1)

    def test_envio_sem_transacao_aberta(self):
        lead = novo_lead()
        estados = []

        def verificar(*args, **kwargs):
            estados.append(connection.in_atomic_block)
            # O "em_andamento" já foi gravado e confirmado antes do HTTP.
            estados.append(LeadIntegracao.objects.get(lead=lead).status)
            return resposta_http(200)

        with patch_post(side_effect=verificar):
            services.executar_notificacao(str(lead.pk))
        self.assertEqual(estados, [False, StatusIntegracao.EM_ANDAMENTO])
