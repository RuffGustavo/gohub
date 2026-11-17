---
paths:
  - "**/tests.py"
  - "**/tests/**/*.py"
  - "**/test_*.py"
---
# Testes

- Runner: `python manage.py test [app.<nome>]` (Django). Não há pytest; não adicione sem aprovação.
- Usa Postgres real (cria `test_<POSTGRES_DB>`); o usuário precisa de CREATEDB. Nunca aponte testes para banco de produção/compartilhado.
- API: `rest_framework.test.APITestCase` + `self.client.force_authenticate(user)`; demais: `django.test.TestCase`.
- Todo endpoint cobre no mínimo: anônimo → 401; autenticado sem permissão do model → 403; usuário A no objeto de B → 404 (quando houver dono); payload inválido/malicioso → 400; campos read-only enviados são ignorados; caminho feliz.
- Conceda permissões explicitamente (`user.user_permissions.add(Permission.objects.get(codename=...))`); não use superuser para mascarar falta de permissão.
- Credenciais e dados fictícios criados no próprio teste; nada lido do `.env`.
- Tasks: chame a função diretamente ou `.apply()`; não dependa de worker.
- Nunca desative CSRF, validação ou permissões para um teste passar.
