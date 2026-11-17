"""Regras: normalizadores, model, services e selectors."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from ..models import Lead, LeadClassificacao, LeadStatusHistorico, Sistema
from ..selectors import filtrar_leads, historico_classificacoes, historico_status, leads_ativos
from ..services import anonimizar_lead, reclassificar_lead, registrar_lead
from ..validators import normalizar_email, normalizar_obrigatorio, normalizar_telefone, normalizar_texto
from .base import CONFIG_TESTE, criar_lead, criar_usuario, DADOS_LEAD


class NormalizadoresTests(SimpleTestCase):
    def test_texto(self):
        self.assertEqual(normalizar_texto("  Ana \t  Souza\n"), "Ana Souza")
        self.assertEqual(normalizar_texto(None), "")

    def test_obrigatorio(self):
        self.assertEqual(normalizar_obrigatorio(" Ab ", minimo=2, mensagem="x"), "Ab")
        for valor in ("", "   ", None, " A "):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                normalizar_obrigatorio(valor, minimo=2, mensagem="x")

    def test_email(self):
        self.assertEqual(normalizar_email("  Ana.Souza@Empresa.COM "), "ana.souza@empresa.com")
        for valor in ("", None, "   ", "ana", "ana@", "ana @x.com", "a@b", "x" * 250 + "@a.com"):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                normalizar_email(valor)

    def test_telefone(self):
        casos = {
            "(11) 98765-4321": "+5511987654321",
            "11987654321": "+5511987654321",
            "+55 11 98765-4321": "+5511987654321",
            "+1 650-253-0000": "+16502530000",
            "(21) 3333-4444": "+552133334444",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(normalizar_telefone(entrada), esperado)
        for valor in ("", None, "   ", "123", "abc", "(11) 1234", "9" * 16, "1" * 31, "+55 00 00000-0000"):
            with self.subTest(valor=valor), self.assertRaises(ValidationError):
                normalizar_telefone(valor)


@override_settings(POLITICA_PRIVACIDADE_VERSAO="2.1")
class RegistrarLeadTests(TestCase):
    def test_cadastro_normaliza_e_grava_consentimento(self):
        with self.captureOnCommitCallbacks(execute=True):
            lead_id, criado = registrar_lead(**DADOS_LEAD)
        self.assertTrue(criado)
        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual((lead.nome, lead.email, lead.telefone), ("Ana Souza", "ana.souza@empresa.com", "+5511987654321"))
        self.assertEqual(lead.politica_versao, "2.1")
        self.assertIsNotNone(lead.consentimento_em)
        self.assertEqual(lead.historico_status.count(), 1)

    def test_duplicado_nao_grava_nem_consulta(self):
        registrar_lead(**DADOS_LEAD)
        with self.assertNumQueries(3):  # SAVEPOINT, INSERT ... ON CONFLICT, RELEASE
            resultado = registrar_lead(**{**DADOS_LEAD, "email": "ANA.SOUZA@empresa.com ", "nome": "Outra"})
        self.assertEqual(resultado, (None, False))
        self.assertEqual(Lead.objects.get().nome, "Ana Souza")

    def test_dado_invalido_nao_chega_ao_banco(self):
        with self.assertNumQueries(0), self.assertRaises(ValidationError):
            registrar_lead(**{**DADOS_LEAD, "telefone": "123"})

    def test_auditoria_sem_dados_pessoais(self):
        with self.assertLogs("gohub.auditoria", "INFO") as logs, self.captureOnCommitCallbacks(execute=True):
            lead_id, _ = registrar_lead(**DADOS_LEAD)
        self.assertIn(f"evento=lead_captado ip=- lead_id={lead_id}", logs.output[0])
        self.assertNotIn("ana", " ".join(logs.output).lower())


@override_settings(POLITICA_PRIVACIDADE_VERSAO="1.0")
class ReclassificarEAnonimizarTests(TestCase):
    def setUp(self):
        self.usuario = criar_usuario()
        self.lead = criar_lead()

    def test_reclassificacao_manual_preserva_historico(self):
        n8n = Sistema.por_codigo(Sistema.N8N)
        LeadClassificacao.objects.create(lead=self.lead, classificacao="frio", metodo="ia", confianca="0.87", sistema=n8n)
        with self.assertLogs("gohub.auditoria", "INFO") as logs, self.captureOnCommitCallbacks(execute=True):
            registro = reclassificar_lead(
                lead=self.lead, classificacao="morno", justificativa="  Pediu retorno  ", usuario=self.usuario
            )
        self.assertEqual((registro.metodo, registro.usuario, registro.justificativa), ("manual", self.usuario, "Pediu retorno"))
        self.assertEqual(self.lead.classificacao_atual, "morno")
        self.assertEqual(list(self.lead.classificacoes.values_list("metodo", flat=True)), ["manual", "ia"])
        self.assertIn(f"usuario_id={self.usuario.pk} lead_id={self.lead.pk} classificacao=morno", logs.output[0])
        self.assertNotIn("Pediu", logs.output[0])

    def test_reclassificacao_invalida(self):
        for dados in ({"classificacao": "fervendo", "justificativa": "x"},
                      {"classificacao": "quente", "justificativa": "   "},
                      {"classificacao": "quente", "justificativa": "x" * 2001}):
            with self.subTest(dados=dados), self.assertRaises(ValidationError):
                reclassificar_lead(lead=self.lead, usuario=self.usuario, **dados)
        self.assertFalse(self.lead.classificacoes.exists())

    def test_anonimizar(self):
        with self.assertLogs("gohub.auditoria", "INFO") as logs, self.captureOnCommitCallbacks(execute=True):
            anonimizar_lead(lead=self.lead, usuario=self.usuario)
        self.assertEqual(self.lead.nome, "ANONIMIZADO")
        self.assertIn(f"evento=lead_anonimizado ip=- usuario_id={self.usuario.pk} lead_id={self.lead.pk}", logs.output[0])
        with self.assertRaisesMessage(ValidationError, "já foi anonimizado"):
            anonimizar_lead(lead=self.lead, usuario=self.usuario)

    def test_reclassificar_lead_excluido_recusado_pelo_banco(self):
        self.lead.anonimizar()
        with self.assertRaises(IntegrityError):
            reclassificar_lead(lead=self.lead, classificacao="frio", justificativa="x", usuario=self.usuario)


@override_settings(POLITICA_PRIVACIDADE_VERSAO="1.0")
class SelectorsTests(TestCase):
    def setUp(self):
        self.ana = criar_lead()
        self.bruno = criar_lead(nome="Bruno", email="bruno@beta.com", empresa="Beta 100%")
        n8n = Sistema.por_codigo(Sistema.N8N)
        LeadClassificacao.objects.create(lead=self.ana, classificacao="quente", metodo="regra", sistema=n8n)
        LeadStatusHistorico.objects.create(lead=self.ana, status_novo="enviado_crm", sistema=n8n)

    def ids(self, **filtros):
        return [lead.pk for lead in filtrar_leads(**filtros)]

    def test_filtros(self):
        self.assertEqual(self.ids(), [self.bruno.pk, self.ana.pk])
        self.assertEqual(self.ids(status="enviado_crm"), [self.ana.pk])
        self.assertEqual(self.ids(classificacao="quente"), [self.ana.pk])
        self.assertEqual(self.ids(classificacao="sem"), [self.bruno.pk])
        self.assertEqual(self.ids(busca="BETA"), [self.bruno.pk])
        self.assertEqual(self.ids(busca="100%"), [self.bruno.pk])
        self.assertEqual(self.ids(busca="%"), [self.bruno.pk])
        self.assertEqual(self.ids(busca="'; DROP TABLE lead; --"), [])

    def test_valores_fora_das_choices_ignorados(self):
        self.assertEqual(len(self.ids(status="x' OR 1=1", classificacao="nada")), 2)

    def test_periodo_com_fim_inclusivo(self):
        hoje = timezone.localdate()
        self.assertEqual(len(self.ids(inicio=hoje, fim=hoje)), 2)
        self.assertEqual(self.ids(fim=hoje - datetime.timedelta(days=1)), [])
        self.assertEqual(self.ids(inicio=hoje + datetime.timedelta(days=1)), [])

    def test_excluidos_fora(self):
        self.bruno.anonimizar()
        self.assertEqual(self.ids(), [self.ana.pk])
        self.assertFalse(leads_ativos().filter(pk=self.bruno.pk).exists())

    def test_listagem_le_so_colunas_da_lista(self):
        sql = str(filtrar_leads().query)
        self.assertNotIn("consentimento_em", sql)
        self.assertNotIn("JOIN", sql)

    def test_historicos_sem_n_mais_1(self):
        with self.assertNumQueries(2):
            status = list(historico_status(self.ana))
            classificacoes = list(historico_classificacoes(self.ana))
            [h.autor for h in status + classificacoes]
        self.assertEqual([h.status_novo for h in status], ["enviado_crm", "recebido"])


@override_settings(**CONFIG_TESTE)
class ModelTests(TestCase):
    def setUp(self):
        self.lead = criar_lead()
        self.usuario = criar_usuario()
        self.n8n = Sistema.por_codigo(Sistema.N8N)

    def classificacao(self, **dados):
        return LeadClassificacao(lead=self.lead, classificacao="frio", **dados)

    def test_clean_autoria_e_metodo(self):
        invalidos = {
            "sem autor": {"metodo": "regra"},
            "dois autores": {"metodo": "regra", "sistema": self.n8n, "usuario": self.usuario},
            "manual sem usuário": {"metodo": "manual", "sistema": self.n8n},
            "regra sem sistema": {"metodo": "regra", "usuario": self.usuario},
            "IA com confiança acima de 1": {"metodo": "ia", "sistema": self.n8n, "confianca": Decimal("1.5")},
            "IA com confiança negativa": {"metodo": "ia", "sistema": self.n8n, "confianca": Decimal("-0.1")},
            "confiança fora da IA": {"metodo": "regra", "sistema": self.n8n, "confianca": Decimal("0.5")},
        }
        for caso, dados in invalidos.items():
            with self.subTest(caso), self.assertRaises(ValidationError):
                self.classificacao(**dados).clean()
        self.classificacao(metodo="ia", sistema=self.n8n, confianca=Decimal("0.87")).clean()
        self.classificacao(metodo="manual", usuario=self.usuario).clean()
        for dados in ({}, {"sistema": self.n8n, "usuario": self.usuario}):
            with self.assertRaises(ValidationError):
                LeadStatusHistorico(lead=self.lead, status_novo="erro", **dados).clean()

    def test_save_de_instancia_antiga_nao_regrava_snapshot(self):
        antigo = Lead.objects.get(pk=self.lead.pk)
        LeadStatusHistorico.objects.create(lead=self.lead, status_novo="em_nutricao", sistema=self.n8n)
        LeadClassificacao.objects.create(lead=self.lead, classificacao="quente", metodo="regra", sistema=self.n8n)
        antigo.cargo = "  CTO  "  # instância lida antes das mudanças
        antigo.save()
        self.assertEqual(antigo.cargo, "CTO")  # normalizado também em memória
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.cargo, self.lead.status_atual, self.lead.classificacao_atual),
                         ("CTO", "em_nutricao", "quente"))

    def test_save_de_instancia_antiga_nao_desfaz_anonimizacao(self):
        antigo = Lead.objects.get(pk=self.lead.pk)
        anonimizar_lead(lead=self.lead, usuario=self.usuario)
        antigo.cargo = "CTO"
        with self.assertRaises(IntegrityError), transaction.atomic():
            antigo.save()
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.nome, "ANONIMIZADO")

    def test_exclusao_fisica_bloqueada(self):
        with self.assertRaises(ValidationError):
            self.lead.delete()
        with self.assertRaises(ValidationError):
            Lead.objects.all().delete()
        self.assertTrue(Lead.objects.filter(pk=self.lead.pk).exists())

    def test_historicos_imutaveis_em_python(self):
        registro = self.lead.historico_status.get()
        registro.observacao = "alterada"
        for acao in (registro.save, registro.delete, lambda: LeadStatusHistorico.objects.update(observacao="x"),
                     lambda: LeadStatusHistorico.objects.all().delete(), lambda: LeadClassificacao.objects.all().delete()):
            with self.subTest(acao=acao), self.assertRaises(ValidationError):
                acao()
        self.assertEqual(self.lead.historico_status.get().observacao, "Lead recebido pelo formulário público")

    def test_str_sem_email(self):
        self.assertEqual(str(self.lead), "Ana Souza")
        self.assertEqual(str(self.lead.historico_status.get()), "— → Recebido")
