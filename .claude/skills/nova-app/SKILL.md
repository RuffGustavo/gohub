---
name: nova-app
description: Cria uma nova app Django de domínio no GoHub em app/<nome>, registra em INSTALLED_APPS e planeja segurança. Use ao iniciar um novo módulo ou domínio de negócio.
---
# Nova app Django

Entrada: nome em snake_case, em português (ex.: `eventos`).

1. Verifique se o domínio já pertence a uma app existente (`ls app/`, buscar models relacionados). Não duplique responsabilidades.
2. Se `app/__init__.py` não existir, crie-o vazio.
3. `mkdir -p app/<nome> && python manage.py startapp <nome> app/<nome>`
4. Em `app/<nome>/apps.py`: `name = "app.<nome>"` e `verbose_name` em português.
5. Adicione `"app.<nome>"` ao final de `INSTALLED_APPS` em `setup/settings.py`.
6. Plano de segurança (registre antes de codar): dados tratados (pessoais? financeiros? arquivos?), quem lê/escreve, se há dono por objeto, uploads, integrações externas, tasks.
7. Models → skill `migration`. Endpoints → skill `novo-endpoint`. Tasks → `.claude/rules/celery.md`.
8. Conclua com a skill `validar-entrega`.
