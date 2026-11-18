import datetime
from unittest import mock

from django.test import SimpleTestCase, TestCase

from app.users.agente import resumir_user_agent
from app.users.models import RegistroAcesso

from ..periodos import FiltroPeriodoForm, Periodo, percentual, serie, variacao
from .base import CHROME_WINDOWS, SAFARI_IPHONE, acesso, criar_usuario

HOJE = datetime.date(2026, 11, 17)  # terça-feira


@mock.patch("app.dashboard.periodos.hoje", return_value=HOJE)
class PeriodoTests(SimpleTestCase):
    def test_ultimos_dias_inclui_hoje_e_anterior_tem_mesmo_tamanho(self, _):
        periodo = Periodo.ultimos(7)
        self.assertEqual((periodo.inicio, periodo.fim), (datetime.date(2026, 11, 11), HOJE))
        anterior = periodo.anterior()
        self.assertEqual((anterior.inicio, anterior.fim), (datetime.date(2026, 11, 4), datetime.date(2026, 11, 10)))
        self.assertEqual(periodo.granularidade, "dia")
        self.assertEqual(len(periodo.intervalos()), 7)

    def test_limite_final_aberto_no_dia_seguinte(self, _):
        periodo = Periodo(HOJE, HOJE)
        self.assertEqual(periodo.fim_dt - periodo.inicio_dt, datetime.timedelta(days=1))

    def test_intervalos_por_semana_e_mes(self, _):
        self.assertEqual(Periodo.ultimas_semanas(2).intervalos(), [datetime.date(2026, 11, 9), datetime.date(2026, 11, 16)])
        meses = Periodo.ultimos_meses(12).intervalos()
        self.assertEqual((meses[0], meses[-1], len(meses)), (datetime.date(2025, 12, 1), datetime.date(2026, 11, 1), 12))

    def test_granularidade_automatica(self, _):
        self.assertEqual(Periodo.ultimos(90).granularidade, "semana")
        self.assertEqual(Periodo.ultimos(365).granularidade, "mes")

    def test_variacao_sem_base_e_percentual(self, _):
        self.assertIsNone(variacao(5, 0))
        self.assertEqual(variacao(15, 10), 50.0)
        self.assertEqual(percentual(1, 3), 33.3)
        self.assertEqual(percentual(0, 0), 0.0)


@mock.patch("app.dashboard.periodos.hoje", return_value=HOJE)
class FiltroPeriodoFormTests(SimpleTestCase):
    def test_padrao_ultimos_30_dias(self, _):
        periodo = FiltroPeriodoForm({}).periodo_escolhido()
        self.assertEqual(periodo.dias, 30)

    def test_personalizado_valido(self, _):
        form = FiltroPeriodoForm({"periodo": "personalizado", "inicio": "2026-01-01", "fim": "2026-01-31", "granularidade": "semana"})
        self.assertTrue(form.is_valid())
        periodo = form.periodo_escolhido()
        self.assertEqual((periodo.inicio, periodo.fim, periodo.granularidade), (datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), "semana"))

    def test_invalidos_caem_no_padrao(self, _):
        casos = [
            {"periodo": "personalizado"},
            {"periodo": "personalizado", "inicio": "2026-11-10", "fim": "2026-11-01"},
            {"periodo": "personalizado", "inicio": "2024-01-01", "fim": "2026-01-01"},
            {"periodo": "personalizado", "inicio": "2026-11-01", "fim": "2026-12-01"},
            {"periodo": "1d; DROP TABLE lead"},
            {"granularidade": "hora"},
            {"periodo": "personalizado", "inicio": "1900-01-01", "fim": "1900-01-02"},
        ]
        for dados in casos:
            with self.subTest(dados=dados):
                form = FiltroPeriodoForm(dados)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.periodo_escolhido().dias, 30)


class SerieTests(TestCase):
    def test_preenche_zero_nos_intervalos_sem_registro(self):
        usuario = criar_usuario()
        periodo = Periodo.ultimos(3)
        acesso(usuario, quando=periodo.inicio_dt + datetime.timedelta(hours=12))
        acesso(usuario, quando=periodo.inicio_dt + datetime.timedelta(hours=13))
        acesso(usuario, quando=periodo.inicio_dt - datetime.timedelta(hours=1))  # fora do período
        dados = serie(RegistroAcesso.objects.all(), "ocorrido_em", periodo)
        self.assertEqual(dados["total"], [2, 0, 0])
        self.assertEqual(len(dados["rotulos"]), 3)


class AgenteTests(SimpleTestCase):
    def test_resumos(self):
        self.assertEqual(resumir_user_agent(CHROME_WINDOWS), ("Chrome", "Windows", "computador"))
        self.assertEqual(resumir_user_agent(SAFARI_IPHONE), ("Safari", "iOS", "celular"))
        edge = CHROME_WINDOWS + " Edg/130.0"
        self.assertEqual(resumir_user_agent(edge)[0], "Edge")
        ipad = SAFARI_IPHONE.replace("iPhone; CPU iPhone OS", "iPad; CPU OS")
        self.assertEqual(resumir_user_agent(ipad)[2], "tablet")

    def test_clientes_nao_navegador_e_entrada_enorme(self):
        self.assertEqual(resumir_user_agent("curl/8.0"), ("", "", "outro"))
        self.assertEqual(resumir_user_agent(None), ("", "", "outro"))
        self.assertEqual(resumir_user_agent("Mozilla/5.0 " + "A" * 100000)[2], "outro")
