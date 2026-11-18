"""Agregações dos dashboards, só leitura e calculadas no banco (COUNT/GROUP BY).

Quem chama decide o escopo: `resumo_pessoal` SEMPRE recebe o usuário e nunca o lê de
parâmetro externo; as funções gerais (`resumo_*`) só são chamadas depois da checagem de
permissão (app/dashboard/permissions.py).

Definições (exibidas também na tela):
- Acesso: login bem-sucedido gravado em registro_acesso (a contagem começa quando o
  registro foi implantado; logins anteriores não existem no banco). Falha: senha errada
  para um usuário existente.
- Leads: só ativos (excluídos e anonimizados ficam de fora). Cada lead conta uma vez por
  indicador: status pelo snapshot status_atual; etapas do funil pelo histórico (o lead
  alcançou a etapa ao menos uma vez).
- Taxa de conversão: leads recebidos no período que chegaram ao status "Enviado ao CRM"
  ÷ leads recebidos no período. Não há status de venda (convertido/perdido) no sistema.
"""

from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, Max, OuterRef, Q

from app.leads.models import ClassificacaoLead, Lead, LeadStatus, LeadStatusHistorico
from app.users.models import CanalAcesso, DispositivoAcesso, RegistroAcesso, StatusAcesso

from .periodos import Periodo, percentual, serie, variacao

SUCESSO = Q(status=StatusAcesso.SUCESSO)
FALHA = Q(status=StatusAcesso.FALHA)
DESCARTES = [LeadStatus.DUPLICADO, LeadStatus.INVALIDO, LeadStatus.ERRO]
NAO_IDENTIFICADO = "Não identificado"
LIMITE_IPS = 5
LIMITE_CATEGORIAS = 6


def _no(periodo, campo):
    return Q(**periodo.filtro(campo))


def _distribuicao(queryset, campo, rotulos=None, limite=LIMITE_CATEGORIAS):
    """{"rotulos", "valores"} por valor de `campo`, do maior para o menor; o excedente vira "Outros"."""
    linhas = list(queryset.values(campo).annotate(total=Count("pk")).order_by("-total", campo))
    itens = [((rotulos or {}).get(linha[campo], linha[campo]) or NAO_IDENTIFICADO, linha["total"]) for linha in linhas]
    if len(itens) > limite:
        itens = itens[: limite - 1] + [("Outros", sum(total for _, total in itens[limite - 1:]))]
    return {"rotulos": [rotulo for rotulo, _ in itens], "valores": [total for _, total in itens]}


def _acesso_dict(registro):
    if not registro:
        return None
    return {
        "ocorrido_em": registro["ocorrido_em"],
        "ip": registro["ip"],
        "navegador": registro["navegador"] or NAO_IDENTIFICADO,
        "sistema_operacional": registro["sistema_operacional"] or NAO_IDENTIFICADO,
        "dispositivo": DispositivoAcesso(registro["dispositivo"]).label,
    }


CAMPOS_ACESSO = ["ocorrido_em", "ip", "navegador", "sistema_operacional", "dispositivo"]


def ultimos_acessos(usuario):
    """(login atual, acesso anterior, última falha) do próprio usuário; None se não houver."""
    qs = RegistroAcesso.objects.filter(usuario=usuario).order_by("-ocorrido_em", "-id")
    sucessos = list(qs.filter(SUCESSO).values(*CAMPOS_ACESSO)[:2])
    falha = qs.filter(FALHA).values(*CAMPOS_ACESSO).first()
    atual = sucessos[0] if sucessos else None
    anterior = sucessos[1] if len(sucessos) > 1 else None
    return _acesso_dict(atual), _acesso_dict(anterior), _acesso_dict(falha)


# ------------------------------------------------------------------ dashboard pessoal
def resumo_pessoal(usuario, periodo):
    """Indicadores e gráficos de acesso do PRÓPRIO usuário (toda consulta filtra por ele)."""
    qs = RegistroAcesso.objects.filter(usuario=usuario)
    anterior = periodo.anterior()
    dia, semana, mes = Periodo.ultimos(1), Periodo.ultimas_semanas(1), Periodo.ultimos_meses(1)
    contagens = qs.aggregate(
        acessos_total=Count("pk", filter=SUCESSO),
        acessos_periodo=Count("pk", filter=SUCESSO & _no(periodo, "ocorrido_em")),
        acessos_periodo_anterior=Count("pk", filter=SUCESSO & _no(anterior, "ocorrido_em")),
        falhas_periodo=Count("pk", filter=FALHA & _no(periodo, "ocorrido_em")),
        ips_distintos_periodo=Count("ip", distinct=True, filter=SUCESSO & _no(periodo, "ocorrido_em")),
        acessos_hoje=Count("pk", filter=SUCESSO & _no(dia, "ocorrido_em")),
        acessos_semana=Count("pk", filter=SUCESSO & _no(semana, "ocorrido_em")),
        acessos_mes=Count("pk", filter=SUCESSO & _no(mes, "ocorrido_em")),
    )
    contagens["variacao_acessos"] = variacao(contagens["acessos_periodo"], contagens["acessos_periodo_anterior"])

    sucessos = qs.filter(SUCESSO)
    no_periodo = sucessos.filter(**periodo.filtro("ocorrido_em"))
    evolucao = serie(sucessos, "ocorrido_em", periodo)
    janelas = {
        "dia": (Periodo.ultimos(14, granularidade="dia"), "Últimos 14 dias"),
        "semana": (Periodo.ultimas_semanas(12), "Últimas 12 semanas"),
        "mes": (Periodo.ultimos_meses(12), "Últimos 12 meses"),
    }
    por_periodo = {}
    for chave, (janela, descricao) in janelas.items():
        dados = serie(sucessos, "ocorrido_em", janela)
        por_periodo[chave] = {"descricao": descricao, "rotulos": dados["rotulos"], "valores": dados["total"]}
    ips = (
        no_periodo.exclude(ip__isnull=True)
        .values("ip")
        .annotate(acessos=Count("pk"), ultimo_em=Max("ocorrido_em"))
        .order_by("-ultimo_em")[:LIMITE_IPS]
    )
    return {
        "periodo": periodo.como_dict(),
        "indicadores": contagens,
        "graficos": {
            "evolucao": {"rotulos": evolucao["rotulos"], "series": [{"nome": "Acessos", "valores": evolucao["total"]}]},
            "por_periodo": por_periodo,
            "navegadores": _distribuicao(no_periodo, "navegador"),
            "dispositivos": _distribuicao(no_periodo, "dispositivo", dict(DispositivoAcesso.choices)),
        },
        "ips_recentes": list(ips),
    }


# ------------------------------------------------------------------ dashboard gerencial
def resumo_usuarios():
    """Contas não excluídas logicamente; "ativas" = podem entrar (ativo = true)."""
    return get_user_model().objects.filter(deleted_at__isnull=True).aggregate(
        total=Count("pk"),
        ativos=Count("pk", filter=Q(is_active=True)),
        inativos=Count("pk", filter=Q(is_active=False)),
    )


def resumo_acessos(periodo):
    qs = RegistroAcesso.objects.all()
    anterior = periodo.anterior()
    indicadores = qs.aggregate(
        acessos_total=Count("pk", filter=SUCESSO),
        acessos_periodo=Count("pk", filter=SUCESSO & _no(periodo, "ocorrido_em")),
        acessos_periodo_anterior=Count("pk", filter=SUCESSO & _no(anterior, "ocorrido_em")),
        falhas_periodo=Count("pk", filter=FALHA & _no(periodo, "ocorrido_em")),
        usuarios_com_acesso_periodo=Count("usuario", distinct=True, filter=SUCESSO & _no(periodo, "ocorrido_em")),
    )
    indicadores["variacao_acessos"] = variacao(indicadores["acessos_periodo"], indicadores["acessos_periodo_anterior"])
    sucessos = qs.filter(SUCESSO)
    no_periodo = sucessos.filter(**periodo.filtro("ocorrido_em"))
    atividade = serie(sucessos, "ocorrido_em", periodo, acessos=Count("pk"), usuarios=Count("usuario", distinct=True))
    return {
        "indicadores": indicadores,
        "graficos": {
            "atividade": {
                "rotulos": atividade["rotulos"],
                "series": [
                    {"nome": "Acessos", "valores": atividade["acessos"]},
                    {"nome": "Usuários distintos", "valores": atividade["usuarios"]},
                ],
            },
            "canais": _distribuicao(no_periodo, "canal", dict(CanalAcesso.choices)),
            "dispositivos": _distribuicao(no_periodo, "dispositivo", dict(DispositivoAcesso.choices)),
        },
    }


def _alcancou(status):
    return Exists(LeadStatusHistorico.objects.filter(lead=OuterRef("pk"), status_novo=status))


def _leads_com_etapas():
    return Lead.objects.ativos().annotate(
        chegou_crm=_alcancou(LeadStatus.ENVIADO_CRM),
        passou_nutricao=_alcancou(LeadStatus.EM_NUTRICAO),
    )


INDISPONIVEL_ORIGEM = (
    "O sistema não registra a origem (canal de aquisição) dos leads: todos chegam pelo "
    "formulário de contato. Para analisar canais, é preciso passar a gravar esse dado na captação."
)
INDISPONIVEL_RESPONSAVEL = (
    "O sistema não registra responsável, equipe ou departamento dos leads, por isso não há distribuição a exibir."
)


def resumo_leads(periodo):
    base = Lead.objects.ativos()
    anterior = periodo.anterior()
    recebidos = _no(periodo, "created_at")
    por_status = {
        f"status_{codigo}": Count("pk", filter=recebidos & Q(status_atual=codigo)) for codigo in LeadStatus.values
    }
    indicadores = _leads_com_etapas().aggregate(
        total=Count("pk"),
        recebidos_periodo=Count("pk", filter=recebidos),
        recebidos_periodo_anterior=Count("pk", filter=_no(anterior, "created_at")),
        convertidos_periodo=Count("pk", filter=recebidos & Q(chegou_crm=True)),
        convertidos_periodo_anterior=Count("pk", filter=_no(anterior, "created_at") & Q(chegou_crm=True)),
        nutricao_periodo=Count("pk", filter=recebidos & Q(passou_nutricao=True)),
        descartados_periodo=Count("pk", filter=recebidos & Q(status_atual__in=DESCARTES)),
        **por_status,
    )
    indicadores["variacao_recebidos"] = variacao(
        indicadores["recebidos_periodo"], indicadores["recebidos_periodo_anterior"]
    )
    indicadores["taxa_conversao"] = percentual(indicadores["convertidos_periodo"], indicadores["recebidos_periodo"])
    taxa_anterior = percentual(indicadores["convertidos_periodo_anterior"], indicadores["recebidos_periodo_anterior"])
    indicadores["taxa_conversao_anterior"] = taxa_anterior if indicadores["recebidos_periodo_anterior"] else None
    indicadores["variacao_taxa_pontos"] = (
        round(indicadores["taxa_conversao"] - taxa_anterior, 1) if indicadores["recebidos_periodo_anterior"] else None
    )

    evolucao = serie(base, "created_at", periodo)
    meses = Periodo.ultimos_meses(12)
    mensal = serie(
        _leads_com_etapas(), "created_at", meses,
        recebidos=Count("pk"), convertidos=Count("pk", filter=Q(chegou_crm=True)),
    )
    no_periodo = base.filter(**periodo.filtro("created_at"))
    classificacoes = dict(ClassificacaoLead.choices)
    classificacoes[None] = "Não classificado"
    status_labels = dict(LeadStatus.choices)
    return {
        "indicadores": indicadores,
        "graficos": {
            "evolucao": {"rotulos": evolucao["rotulos"], "series": [{"nome": "Leads recebidos", "valores": evolucao["total"]}]},
            "funil": {
                "rotulos": ["Recebidos", "Passaram por nutrição", "Enviados ao CRM"],
                "valores": [
                    indicadores["recebidos_periodo"], indicadores["nutricao_periodo"], indicadores["convertidos_periodo"],
                ],
                "descartados": indicadores["descartados_periodo"],
            },
            "status": {
                "rotulos": [status_labels[codigo] for codigo in LeadStatus.values],
                "valores": [indicadores[f"status_{codigo}"] for codigo in LeadStatus.values],
            },
            "classificacao": _distribuicao(no_periodo, "classificacao_atual", classificacoes),
            "conversao": {
                "rotulos": ["Enviados ao CRM", "Demais"],
                "valores": [
                    indicadores["convertidos_periodo"],
                    indicadores["recebidos_periodo"] - indicadores["convertidos_periodo"],
                ],
            },
            "desempenho_mensal": {
                "descricao": "Últimos 12 meses, pelo mês de recebimento",
                "rotulos": mensal["rotulos"],
                "recebidos": mensal["recebidos"],
                "convertidos": mensal["convertidos"],
                "taxa": [percentual(c, r) for c, r in zip(mensal["convertidos"], mensal["recebidos"])],
            },
        },
        "origem": {"disponivel": False, "motivo": INDISPONIVEL_ORIGEM},
        "responsavel": {"disponivel": False, "motivo": INDISPONIVEL_RESPONSAVEL},
    }
