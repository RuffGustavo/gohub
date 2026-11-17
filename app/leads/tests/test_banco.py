"""Banco: gatilhos, CHECK, FKs, reverso das migrations e concorrência (Entregável 10)."""

import threading

from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from ..models import ClassificacaoLead, Lead, LeadClassificacao, LeadStatus, LeadStatusHistorico, Sistema
from ..services import reclassificar_lead, registrar_lead
from .base import CONFIG_TESTE, criar_lead, criar_usuario, DADOS_LEAD, inserir_lead_sql


class GatilhosTests(TestCase):
    """Cenários do Entregável 10 executados direto no banco (gatilhos e CHECK)."""

    def test_sistemas_iniciais(self):
        self.assertEqual(
            sorted(Sistema.objects.values_list("codigo", flat=True)),
            ["aplicacao_web", "crm", "n8n", "notificacao"],
        )

    def test_normalizacao_e_historico_inicial(self):
        lead = inserir_lead_sql(nome="  Ana   Souza ", email="  Ana.Souza@Empresa.COM ",
                                telefone="+55 11 98765-4321", empresa=" Acme  SA ", cargo=" CEO ")
        self.assertEqual(
            (lead.nome, lead.email, lead.telefone, lead.empresa, lead.cargo),
            ("Ana Souza", "ana.souza@empresa.com", "+5511987654321", "Acme SA", "CEO"),
        )
        self.assertEqual(lead.status_atual, LeadStatus.RECEBIDO)
        self.assertIsNone(lead.classificacao_atual)
        inicial = lead.historico_status.get()
        self.assertEqual((inicial.status_anterior, inicial.status_novo), (None, LeadStatus.RECEBIDO))
        self.assertEqual(inicial.sistema.codigo, Sistema.APLICACAO_WEB)

    def test_status_e_classificacao_atualizam_snapshot(self):
        lead = inserir_lead_sql()
        n8n = Sistema.por_codigo(Sistema.N8N)
        LeadClassificacao.objects.create(lead=lead, classificacao="quente", metodo="regra", sistema=n8n)
        LeadStatusHistorico.objects.create(lead=lead, status_novo="enviado_crm", sistema=n8n)
        lead.refresh_from_db()
        self.assertEqual((lead.classificacao_atual, lead.status_atual), ("quente", "enviado_crm"))
        self.assertEqual(lead.historico_status.first().status_anterior, "recebido")

    def test_evento_atrasado_nao_sobrescreve(self):
        lead = inserir_lead_sql()
        n8n = Sistema.por_codigo(Sistema.N8N)
        LeadStatusHistorico.objects.create(lead=lead, status_novo="enviado_crm", sistema=n8n)
        LeadStatusHistorico.objects.create(
            lead=lead, status_novo="erro", sistema=n8n, ocorrido_em=timezone.now() - timezone.timedelta(hours=1)
        )
        lead.refresh_from_db()
        self.assertEqual(lead.status_atual, "enviado_crm")
        self.assertEqual(lead.historico_status.count(), 3)

    def test_update_direto_do_snapshot_recusado(self):
        lead = inserir_lead_sql()
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE lead SET status_atual = 'duplicado' WHERE id = %s", [lead.pk])

    def test_historicos_imutaveis_no_banco(self):
        lead = inserir_lead_sql()
        for sql in ("UPDATE lead_status_historico SET observacao = 'x' WHERE lead_id = %s",
                    "DELETE FROM lead_status_historico WHERE lead_id = %s"):
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, [lead.pk])

    def test_delete_do_lead_recusado_pela_fk(self):
        lead = inserir_lead_sql()
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM lead WHERE id = %s", [lead.pk])

    def test_email_duplicado_e_invalido(self):
        inserir_lead_sql()
        with self.assertRaises(IntegrityError), transaction.atomic():
            inserir_lead_sql(email=" ANA@exemplo.com ")
        with self.assertRaisesMessage(IntegrityError, "ck_lead_email"), transaction.atomic():
            inserir_lead_sql(email="ana-sem-arroba")
        with self.assertRaisesMessage(IntegrityError, "ck_lead_cargo"), transaction.atomic():
            inserir_lead_sql(email="b@exemplo.com", cargo="   ")

    def test_anonimizacao(self):
        lead = inserir_lead_sql()
        lead.anonimizar()
        self.assertEqual((lead.nome, lead.telefone), ("ANONIMIZADO", "+0"))
        self.assertIsNotNone(lead.deleted_at)
        n8n = Sistema.por_codigo(Sistema.N8N)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LeadStatusHistorico.objects.create(lead=lead, status_novo="erro", sistema=n8n)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LeadClassificacao.objects.create(lead=lead, classificacao="frio", metodo="regra", sistema=n8n)
        # Regravar dados pessoais de um lead anonimizado é recusado (acréscimo ao DDL).
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE lead SET nome = 'Ana' WHERE id = %s", [lead.pk])
        # O mesmo e-mail pode ser recadastrado como lead novo.
        novo = inserir_lead_sql()
        self.assertNotEqual(novo.pk, lead.pk)
        self.assertEqual(novo.classificacao_atual, None)
        self.assertEqual(ClassificacaoLead.values, ["frio", "morno", "quente"])


class ReversoDasMigrationsTests(TransactionTestCase):
    """`migrate leads zero` e reaplicação funcionam (só no banco de teste)."""

    serialized_rollback = True  # restaura os sistemas iniciais para os testes seguintes

    def test_reverter_e_reaplicar(self):
        lead = inserir_lead_sql()
        self.assertTrue(Lead.objects.filter(pk=lead.pk).exists())
        call_command("migrate", "leads", "zero", verbosity=0)
        with connection.cursor() as cursor:
            tabelas = connection.introspection.table_names(cursor)
            cursor.execute("SELECT count(*) FROM pg_proc WHERE proname LIKE 'fn_lead%%' OR proname = 'fn_anonimizar_lead'")
            funcoes = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM pg_proc WHERE proname = 'fn_set_updated_at'")
            set_updated_at = cursor.fetchone()[0]
        self.assertFalse({"lead", "sistema", "lead_status_historico", "lead_classificacao"} & set(tabelas))
        self.assertEqual(funcoes, 0)
        self.assertEqual(set_updated_at, 1)  # pertence a users.0002 e continua lá
        call_command("migrate", "leads", verbosity=0)
        self.assertEqual(inserir_lead_sql().historico_status.count(), 1)


class CenariosAdicionaisTests(TestCase):
    """Complementa o Entregável 10 com as barreiras do banco não cobertas acima."""

    def executar(self, sql, params):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, params)

    def test_cenario_2_ia_e_nutricao(self):
        lead = inserir_lead_sql()
        n8n = Sistema.por_codigo(Sistema.N8N)
        LeadClassificacao.objects.create(lead=lead, classificacao="frio", metodo="ia", confianca="0.8700", sistema=n8n,
                                         versao_criterio="modelo-v1")
        LeadStatusHistorico.objects.create(lead=lead, status_novo="em_nutricao", sistema=n8n)
        lead.refresh_from_db()
        self.assertEqual((lead.classificacao_atual, lead.status_atual), ("frio", "em_nutricao"))

    def test_insert_nao_escolhe_o_estado_inicial(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao, "
                "status_atual, classificacao_atual, classificado_em, deleted_at) "
                "VALUES ('Ana', 'a@exemplo.com', '+5511987654321', 'Acme', 'CEO', now(), '1.0', "
                "'enviado_crm', 'quente', now(), now()) RETURNING id"
            )
            lead = Lead.objects.get(pk=cursor.fetchone()[0])
        self.assertEqual((lead.status_atual, lead.classificacao_atual, lead.deleted_at), ("recebido", None, None))

    def test_campo_obrigatorio_nulo(self):
        with self.assertRaisesMessage(IntegrityError, "cargo"):
            self.executar(
                "INSERT INTO lead (nome, email, telefone, empresa, cargo, consentimento_em, politica_versao) "
                "VALUES ('Ana', 'a@exemplo.com', '+5511987654321', 'Acme', NULL, now(), '1.0')", []
            )
        with self.assertRaisesMessage(IntegrityError, "ck_lead_telefone"):
            inserir_lead_sql(telefone="11 9876")

    def test_checks_dos_historicos(self):
        lead = inserir_lead_sql()
        n8n = Sistema.por_codigo(Sistema.N8N).pk
        casos = {
            "ck_classificacao_manual": ("INSERT INTO lead_classificacao (lead_id, classificacao, metodo, sistema_id) "
                                        "VALUES (%s, 'frio', 'manual', %s)", [lead.pk, n8n]),
            "ck_classificacao_confianca": ("INSERT INTO lead_classificacao (lead_id, classificacao, metodo, sistema_id, "
                                           "confianca) VALUES (%s, 'frio', 'regra', %s, 0.5)", [lead.pk, n8n]),
            "ck_classificacao_valor": ("INSERT INTO lead_classificacao (lead_id, classificacao, metodo, sistema_id) "
                                       "VALUES (%s, 'fervendo', 'regra', %s)", [lead.pk, n8n]),
            "ck_status_hist_autor": ("INSERT INTO lead_status_historico (lead_id, status_novo) VALUES (%s, 'erro')",
                                     [lead.pk]),
            # status_anterior é preenchido pelo gatilho: "recebido" de novo viola o CHECK.
            "ck_status_hist_inicial": ("INSERT INTO lead_status_historico (lead_id, status_novo, sistema_id) "
                                       "VALUES (%s, 'recebido', %s)", [lead.pk, n8n]),
        }
        for constraint, (sql, params) in casos.items():
            with self.subTest(constraint), self.assertRaisesMessage(IntegrityError, constraint):
                self.executar(sql, params)

    def test_updated_at_mantido_pelo_banco(self):
        lead = inserir_lead_sql()
        with connection.cursor() as cursor:
            cursor.execute("UPDATE lead SET cargo = 'CTO', updated_at = '2000-01-01' WHERE id = %s", [lead.pk])
        lead.refresh_from_db()
        self.assertGreater(lead.updated_at.year, 2000)


@override_settings(**CONFIG_TESTE)
class ConcorrenciaTests(TransactionTestCase):
    """Commits reais em conexões separadas (uma por thread)."""

    serialized_rollback = True  # restaura os sistemas iniciais apagados pelo flush

    def em_paralelo(self, funcao, argumentos):
        barreira = threading.Barrier(len(argumentos))
        erros = []

        def executar(argumento):
            try:
                barreira.wait()
                funcao(argumento)
            except Exception as exc:  # registrado e verificado na thread principal
                erros.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=executar, args=(a,)) for a in argumentos]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return erros

    def test_mesmo_email_simultaneo_gera_um_lead(self):
        resultados = []
        erros = self.em_paralelo(
            lambda i: resultados.append(registrar_lead(**{**DADOS_LEAD, "nome": f"Pessoa {i}"})[1]), range(8)
        )
        self.assertEqual(erros, [])
        self.assertEqual(sorted(resultados), [False] * 7 + [True])
        self.assertEqual(Lead.objects.filter(email="ana.souza@empresa.com").count(), 1)

    def test_reclassificacoes_simultaneas_mantem_snapshot_coerente(self):
        lead = criar_lead()
        usuarios = [criar_usuario(f"operador{i}") for i in range(6)]
        valores = ["frio", "morno", "quente"]
        erros = self.em_paralelo(
            lambda i: reclassificar_lead(lead=lead, classificacao=valores[i % 3], justificativa=f"Motivo {i}",
                                         usuario=usuarios[i]),
            range(6),
        )
        self.assertEqual(erros, [])
        lead.refresh_from_db()
        self.assertEqual(lead.classificacoes.count(), 6)
        ultima = lead.classificacoes.order_by("-classificado_em", "-id").first()
        self.assertEqual((lead.classificacao_atual, lead.classificado_em), (ultima.classificacao, ultima.classificado_em))

    def test_status_simultaneos_respeitam_b7_e_b8(self):
        """B8: as gravações são serializadas (FOR UPDATE). B7: o ocorrido_em é calculado antes de
        esperar o lock, então um evento pode terminar depois com carimbo anterior; ele fica no
        histórico sem mudar o snapshot. Por isso status_anterior não forma uma cadeia linear."""
        lead = criar_lead()
        n8n = Sistema.por_codigo(Sistema.N8N)
        novos = ["enviado_crm", "em_nutricao", "erro", "duplicado"]
        erros = self.em_paralelo(
            lambda s: LeadStatusHistorico.objects.create(lead=lead, status_novo=s, sistema=n8n), novos
        )
        self.assertEqual(erros, [])
        eventos = list(lead.historico_status.exclude(status_novo="recebido"))
        self.assertEqual(sorted(e.status_novo for e in eventos), sorted(novos))
        for evento in eventos:  # cada um registra um status que de fato vigorava
            self.assertIn(evento.status_anterior, ["recebido", *novos])
            self.assertNotEqual(evento.status_anterior, evento.status_novo)
        lead.refresh_from_db()
        mais_recente = max(e.ocorrido_em for e in eventos)
        self.assertIn(lead.status_atual, [e.status_novo for e in eventos if e.ocorrido_em == mais_recente])
        self.assertEqual(lead.status_atualizado_em, mais_recente)
