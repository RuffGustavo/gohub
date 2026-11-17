from django.db import migrations

# Dados de domínio da tabela "sistema" (.knowledge/leads_ddl.sql, seção 5). Sem credenciais.
# "aplicacao_web" é obrigatório: o gatilho fn_lead_status_inicial (0003) o usa como autor
# do primeiro registro de status de todo lead.
SISTEMAS = [
    ("aplicacao_web", "Aplicação web (formulário e painel)"),
    ("n8n", "n8n — orquestração de automações"),
    ("crm", "CRM"),
    ("notificacao", "Serviço de notificação"),
]


def criar_sistemas(apps, schema_editor):
    Sistema = apps.get_model("leads", "Sistema")
    for codigo, nome in SISTEMAS:
        Sistema.objects.get_or_create(codigo=codigo, defaults={"nome": nome})


def remover_sistemas(apps, schema_editor):
    # Só remove os que nenhum histórico referencia (FK ON DELETE RESTRICT); os demais
    # somem junto com a tabela ao reverter a 0001.
    Sistema = apps.get_model("leads", "Sistema")
    Sistema.objects.filter(
        codigo__in=[codigo for codigo, _ in SISTEMAS],
        classificacoes_lead__isnull=True,
        historico_status_lead__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leads", "0001_lead"),
    ]

    operations = [
        migrations.RunPython(criar_sistemas, reverse_code=remover_sistemas),
    ]
