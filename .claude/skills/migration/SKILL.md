---
name: migration
description: Cria, revisa e aplica migrations Django no GoHub com classificação de risco e aprovação para operações destrutivas. Use ao alterar models ou antes de rodar migrate.
---
# Migrations

1. Altere a model e gere: `python manage.py makemigrations <app> --name <descricao_curta>`.
2. Revise o arquivo e o SQL: `python manage.py sqlmigrate <app> <numero>`.
3. Classifique:
   - **Seguro**: nova tabela; coluna nullable ou com default; índice em tabela pequena.
   - **Atenção**: `NOT NULL` sem default com dados existentes; `unique` em dados existentes; mudança de tipo; rename (confirme que não virou drop + add); índice em tabela grande (lock).
   - **Destrutivo**: remover coluna/tabela/model; `RunSQL`/`RunPython` que apaga ou reescreve dados. → Pare e peça aprovação explícita, descrevendo a perda de dados e como reverter.
4. Data migration: `migrations.RunPython(forward, reverse)` com `apps.get_model(...)`; nunca importe a model diretamente; sem segredos.
5. Verifique: `python manage.py makemigrations --check --dry-run` e `python manage.py migrate --plan`.
6. Aplicar: o Postgres é uma stack externa e pode ser compartilhado → confirme com o usuário antes de `python manage.py migrate` (ou `docker compose run --rm web python manage.py migrate`). Produção: não aplique; entregue o comando e o plano.
7. Não edite migration já aplicada fora do seu ambiente; crie outra.
