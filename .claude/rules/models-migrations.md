---
paths:
  - "**/models.py"
  - "**/models/**/*.py"
  - "**/migrations/*.py"
  - "**/admin.py"
---
# Models, migrations e admin

- PK implícita `BigAutoField` (`DEFAULT_AUTO_FIELD`): não declare `id`.
- `verbose_name`, `verbose_name_plural`, `help_text` em português; sempre `__str__`; `Meta.ordering` em models listados via API (paginação sem ordem gera `UnorderedObjectListWarning`).
- Usuário: `settings.AUTH_USER_MODEL` em FKs, `get_user_model()` em código; nunca importe `User` direto. `on_delete` deliberado (`PROTECT` para o que não pode sumir em cascata).
- Integridade no banco, não só em Python: `unique`, `UniqueConstraint`, `CheckConstraint`, `null=False` por padrão.
- Concorrência: contadores/saldos com `F()`; leitura-modifica-escrita crítica em `transaction.atomic()` + `select_for_update()`.
- SQL cru só se inevitável e com `cursor.execute(sql, params)`; nunca f-string/format; sem `.extra()`.
- Não armazene segredos/tokens em texto puro sem decisão explícita; senhas só pelo sistema de auth do Django.
- Migrations: sempre via `makemigrations`; não edite migration já aplicada fora do seu ambiente; `RunPython` sempre com `reverse_code`. Procedimento e operações destrutivas: skill `migration`.
- Admin (Jazzmin): `list_display`, `search_fields`, `list_filter` explícitos; campos de controle em `readonly_fields`; sem campos sensíveis em listas; não sobrescreva `has_*_permission` para ampliar acesso.
