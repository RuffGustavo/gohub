"""Forms: captação pública, filtros e reclassificação."""

import datetime

from django.test import SimpleTestCase

from ..forms import FiltroLeadForm, LeadPublicoForm, ReclassificacaoForm
from .base import FORM_VALIDO


class LeadPublicoFormTests(SimpleTestCase):
    def test_valido_normaliza(self):
        form = LeadPublicoForm(FORM_VALIDO)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "ana.souza@empresa.com")
        self.assertEqual(form.cleaned_data["telefone"], "+5511987654321")
        self.assertFalse(form.is_bot)

    def test_obrigatorios_ausentes_ou_so_espacos(self):
        for campo in ("nome", "email", "telefone", "empresa", "cargo", "aceite_politica"):
            for valor in (None, "false" if campo == "aceite_politica" else "   "):
                dados = {**FORM_VALIDO}
                if valor is None:
                    del dados[campo]
                else:
                    dados[campo] = valor
                with self.subTest(campo=campo, valor=valor):
                    form = LeadPublicoForm(dados)
                    self.assertFalse(form.is_valid())
                    self.assertIn(campo, form.errors)

    def test_limites_de_tamanho(self):
        limites = {"nome": 150, "email": 254, "empresa": 150, "cargo": 100}
        for campo, limite in limites.items():
            no_limite = "a" * limite if campo != "email" else "a" * (limite - 6) + "@a.com"
            with self.subTest(campo=campo):
                self.assertTrue(LeadPublicoForm({**FORM_VALIDO, campo: no_limite}).is_valid())
                self.assertIn(campo, LeadPublicoForm({**FORM_VALIDO, campo: no_limite + "a"}).errors)

    def test_aceite_falso_e_honeypot(self):
        self.assertIn("aceite_politica", LeadPublicoForm({**FORM_VALIDO, "aceite_politica": ""}).errors)
        self.assertTrue(LeadPublicoForm({**FORM_VALIDO, "website": "http://spam"}).is_bot)
        self.assertTrue(LeadPublicoForm({"website": "x"}).is_bot)


class FiltroLeadFormTests(SimpleTestCase):
    def test_filtros_validos(self):
        form = FiltroLeadForm({"status": "erro", "classificacao": "sem", "inicio": "2026-01-01",
                               "fim": "2026-01-31", "q": "  acme   sa "})
        self.assertEqual(form.filtros(), {"status": "erro", "classificacao": "sem",
                                          "inicio": datetime.date(2026, 1, 1), "fim": datetime.date(2026, 1, 31),
                                          "busca": "acme sa"})

    def test_invalidos_nao_filtram(self):
        form = FiltroLeadForm({"status": "x' OR 1=1", "classificacao": "nada", "inicio": "31/31/2026",
                               "q": "a"})
        self.assertEqual(form.filtros(), {"status": None, "classificacao": None, "inicio": None,
                                          "fim": None, "busca": None})
        self.assertEqual(set(form.errors), {"status", "classificacao", "inicio", "q"})
        self.assertIn("q", FiltroLeadForm({"q": "x" * 101}).errors)

    def test_fim_antes_do_inicio(self):
        form = FiltroLeadForm({"inicio": "2026-02-01", "fim": "2026-01-01"})
        self.assertIn("fim", form.errors)
        self.assertIsNone(form.filtros()["fim"])


class ReclassificacaoFormTests(SimpleTestCase):
    def test_validacao(self):
        self.assertTrue(ReclassificacaoForm({"classificacao": "quente", "justificativa": " Ok "}).is_valid())
        self.assertIn("classificacao", ReclassificacaoForm({"classificacao": "x", "justificativa": "a"}).errors)
        self.assertIn("justificativa", ReclassificacaoForm({"classificacao": "frio", "justificativa": "  "}).errors)
        self.assertTrue(ReclassificacaoForm({"classificacao": "frio", "justificativa": "a" * 2000}).is_valid())
        self.assertIn("justificativa", ReclassificacaoForm({"classificacao": "frio", "justificativa": "a" * 2001}).errors)
