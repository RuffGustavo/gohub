---
name: validar-entrega
description: Checklist final do GoHub antes de concluir qualquer tarefa que alterou código ou configuração — checks do Django, migrations, testes, revisão de segurança do diff e relatório. Use sempre ao terminar uma implementação.
---
# Validar entrega

1. `python manage.py check`
2. `python manage.py makemigrations --check --dry-run` (pendência = migration faltando).
3. `python manage.py test` (ou `test app.<nome>`). Se o banco estiver inacessível, relate; não pule em silêncio.
4. Mexeu em settings/infra: `DJANGO_DEBUG=false python manage.py check --deploy` — nenhum aviso novo além de W004, W008, W012, W016 (e W009 com o `.env` local).
5. Revise `git status` e `git diff`:
   - sem segredos, `.env`, dumps, `print()` ou logs com dados sensíveis;
   - nenhum controle da seção Segurança do `CLAUDE.md` removido ou afrouxado;
   - nova variável → `.env.example`; nova lib → `requirements.txt` com `==`;
   - todo endpoint/view novo com autorização no backend e isolamento por dono quando aplicável;
   - sem `|safe` com dado de usuário, SQL cru não parametrizado, `AllowAny`, `fields = "__all__"`.
6. Mudança sensível (auth, permissões, uploads, dados pessoais, settings de segurança, Docker, dependências): ofereça o agent `security-auditor` ou `/security-review`.
7. Relatório: o que mudou; comandos executados e resultado real; o que não pôde ser verificado; riscos remanescentes classificados (comprovado / potencial / recomendação).
