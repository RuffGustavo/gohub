"""Período de análise dos dashboards: filtro (form), intervalos e séries por dia/semana/mês.

Convenções (valem para todos os indicadores):
- `inicio` e `fim` são datas inclusivas no fuso do projeto (America/Sao_Paulo); nas consultas
  viram o intervalo semiaberto [inicio 00:00, fim + 1 dia 00:00).
- "Período anterior" tem a mesma quantidade de dias e termina na véspera de `inicio`.
- Séries sempre trazem todos os intervalos do período, com zero onde não há registro.
"""

import datetime
from dataclasses import dataclass

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Count, DateField
from django.db.models.functions import Trunc
from django.utils import timezone

MAX_DIAS = 366
DATA_MINIMA = datetime.date(2000, 1, 1)

DIA, SEMANA, MES = "dia", "semana", "mes"
GRANULARIDADES = [(DIA, "Por dia"), (SEMANA, "Por semana"), (MES, "Por mês")]
_KIND = {DIA: "day", SEMANA: "week", MES: "month"}

PRESETS = {"7d": 7, "30d": 30, "90d": 90, "12m": 365}
PERSONALIZADO = "personalizado"
PERIODOS = [
    ("7d", "Últimos 7 dias"),
    ("30d", "Últimos 30 dias"),
    ("90d", "Últimos 90 dias"),
    ("12m", "Últimos 12 meses"),
    (PERSONALIZADO, "Personalizado"),
]
PERIODO_PADRAO = "30d"

_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def hoje():
    return timezone.localdate()


def _inicio_do_dia(data):
    return timezone.make_aware(datetime.datetime.combine(data, datetime.time.min))


def _somar_meses(data, meses):
    total = data.year * 12 + (data.month - 1) + meses
    return datetime.date(total // 12, total % 12 + 1, 1)


def granularidade_automatica(dias):
    if dias <= 31:
        return DIA
    if dias <= 120:
        return SEMANA
    return MES


@dataclass(frozen=True)
class Periodo:
    inicio: datetime.date
    fim: datetime.date
    granularidade: str = DIA

    @classmethod
    def ultimos(cls, dias, granularidade=None, ate=None):
        fim = ate or hoje()
        inicio = fim - datetime.timedelta(days=dias - 1)
        return cls(inicio, fim, granularidade or granularidade_automatica(dias))

    @classmethod
    def ultimos_meses(cls, meses, ate=None):
        """Os `meses` meses de calendário até o mês de `ate` (inclusive), por mês."""
        fim = ate or hoje()
        return cls(_somar_meses(fim.replace(day=1), -(meses - 1)), fim, MES)

    @classmethod
    def ultimas_semanas(cls, semanas, ate=None):
        """As `semanas` semanas (segunda a domingo) até a semana de `ate`, por semana."""
        fim = ate or hoje()
        segunda = fim - datetime.timedelta(days=fim.weekday())
        return cls(segunda - datetime.timedelta(weeks=semanas - 1), fim, SEMANA)

    @property
    def dias(self):
        return (self.fim - self.inicio).days + 1

    @property
    def inicio_dt(self):
        return _inicio_do_dia(self.inicio)

    @property
    def fim_dt(self):
        """Limite aberto: 00:00 do dia seguinte a `fim`."""
        return _inicio_do_dia(self.fim + datetime.timedelta(days=1))

    def filtro(self, campo):
        return {f"{campo}__gte": self.inicio_dt, f"{campo}__lt": self.fim_dt}

    def anterior(self):
        fim = self.inicio - datetime.timedelta(days=1)
        return Periodo(fim - datetime.timedelta(days=self.dias - 1), fim, self.granularidade)

    @property
    def rotulo(self):
        if self.inicio == self.fim:
            return f"{self.inicio:%d/%m/%Y}"
        return f"{self.inicio:%d/%m/%Y} a {self.fim:%d/%m/%Y}"

    def intervalos(self):
        """Início de cada intervalo (dia, segunda-feira ou dia 1) que toca o período."""
        if self.granularidade == MES:
            atual, passo = self.inicio.replace(day=1), None
        elif self.granularidade == SEMANA:
            atual, passo = self.inicio - datetime.timedelta(days=self.inicio.weekday()), 7
        else:
            atual, passo = self.inicio, 1
        resultado = []
        while atual <= self.fim:
            resultado.append(atual)
            atual = _somar_meses(atual, 1) if passo is None else atual + datetime.timedelta(days=passo)
        return resultado

    def rotular(self, data):
        if self.granularidade == MES:
            return f"{_MESES[data.month - 1]}/{data:%y}"
        if self.granularidade == SEMANA:
            return f"Sem. {data:%d/%m}"
        return f"{data:%d/%m}"

    def como_dict(self):
        anterior = self.anterior()
        return {
            "inicio": self.inicio.isoformat(),
            "fim": self.fim.isoformat(),
            "dias": self.dias,
            "rotulo": self.rotulo,
            "granularidade": self.granularidade,
            "anterior": {"inicio": anterior.inicio.isoformat(), "fim": anterior.fim.isoformat(),
                         "rotulo": anterior.rotulo},
        }


def serie(queryset, campo, periodo, **agregados):
    """Agrega `queryset` por intervalo de `campo` (datetime) dentro do período.

    Sem `agregados`, conta linhas ("total"). Retorna {"rotulos": [...], "<nome>": [...]} com
    um valor por intervalo, zero onde não houve registro. Uma consulta (GROUP BY no banco).
    """
    agregados = agregados or {"total": Count("pk")}
    trunc = Trunc(campo, _KIND[periodo.granularidade], output_field=DateField())
    linhas = (
        queryset.filter(**periodo.filtro(campo))
        .annotate(_intervalo=trunc)
        .values("_intervalo")
        .annotate(**agregados)
        .order_by("_intervalo")
    )
    por_intervalo = {linha["_intervalo"]: linha for linha in linhas}
    intervalos = periodo.intervalos()
    resultado = {"rotulos": [periodo.rotular(data) for data in intervalos]}
    for nome in agregados:
        resultado[nome] = [por_intervalo.get(data, {}).get(nome) or 0 for data in intervalos]
    return resultado


def variacao(atual, anterior):
    """Variação percentual (1 casa); None quando não há base de comparação (anterior = 0)."""
    if not anterior:
        return None
    return round((atual - anterior) * 100 / anterior, 1)


def percentual(parte, total):
    return round(parte * 100 / total, 1) if total else 0.0


class FiltroPeriodoForm(forms.Form):
    """Filtro de período por GET (telas e API). Inválido → erros nos campos e período padrão."""

    periodo = forms.ChoiceField(label="Período", choices=PERIODOS, required=False)
    inicio = forms.DateField(label="De", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fim = forms.DateField(label="Até", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    granularidade = forms.ChoiceField(
        label="Agrupar", choices=[("", "Automático")] + GRANULARIDADES, required=False
    )

    def clean(self):
        dados = super().clean()
        if dados.get("periodo") != PERSONALIZADO:
            return dados
        inicio, fim = dados.get("inicio"), dados.get("fim")
        if not inicio:
            self.add_error("inicio", "Informe a data inicial do período personalizado.")
        if not fim:
            self.add_error("fim", "Informe a data final do período personalizado.")
        if not (inicio and fim):
            return dados
        if inicio < DATA_MINIMA:
            self.add_error("inicio", f"Use uma data a partir de {DATA_MINIMA:%d/%m/%Y}.")
        if fim > hoje():
            self.add_error("fim", "A data final não pode estar no futuro.")
        if fim < inicio:
            self.add_error("fim", "A data final deve ser igual ou posterior à inicial.")
        elif (fim - inicio).days + 1 > MAX_DIAS:
            raise ValidationError(f"O período personalizado pode ter no máximo {MAX_DIAS} dias.")
        return dados

    def periodo_escolhido(self):
        """Periodo do filtro válido; o padrão (últimos 30 dias) quando inválido ou vazio."""
        if not self.is_valid():
            return Periodo.ultimos(PRESETS[PERIODO_PADRAO])
        dados = self.cleaned_data
        granularidade = dados.get("granularidade") or None
        if dados.get("periodo") == PERSONALIZADO:
            inicio, fim = dados["inicio"], dados["fim"]
            return Periodo(inicio, fim, granularidade or granularidade_automatica((fim - inicio).days + 1))
        return Periodo.ultimos(PRESETS[dados.get("periodo") or PERIODO_PADRAO], granularidade)
