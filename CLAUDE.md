# GoHub

Backend Django 5.2 + DRF em estágio inicial: existe apenas o projeto `setup/` (settings, urls, celery). Ainda não há apps de domínio, models, endpoints, tasks nem testes. Idioma do projeto: português (comentários, docs, `verbose_name`, mensagens).

## Stack
- Python 3.13 (`.python-version`), Django 5.2, DRF 3.18, django-filter, drf-yasg (Swagger/ReDoc), Jazzmin (admin).
- PostgreSQL (psycopg2), Celery 5.7 + Redis (só broker, sem result backend), MinIO (só variáveis em settings; sem cliente).
- WhiteNoise (estáticos, manifest), Gunicorn (produção). Dependências fixadas com `==` em `requirements.txt`.
- Frontend: tema DashLite em `setup/static/` + `templates/exemplos/` (HTML estático de referência, não servido).

## Estrutura
- `setup/settings.py` — toda config vem de env via `os.getenv`/`env_bool`/`env_list`; nova variável também entra em `.env.example`.
- `setup/urls.py` — `/admin/`, `/health/`, `/api/` (`router = DefaultRouter()`), `/swagger/`, `/redoc/`.
- `setup/celery.py` — `autodiscover_tasks()` encontra `tasks.py` das apps em `INSTALLED_APPS`.
- `app/` — apps de domínio em `app/<nome>` (skill `nova-app`).
- `templates/base.html` (documento) → `templates/layouts/painel.html` (layout do tema; páginas estendem este) + `templates/partials/`. Ajustes de CSS do tema só em `setup/static/assets/css/gohub.css`.
- `templates/` e `setup/static/` são fontes; `static/` (collectstatic) e `media/` são gerados: não edite.

## Comandos
venv local (requer `.env` e Postgres/Redis acessíveis em localhost):
- `source .venv/bin/activate`
- `python manage.py runserver` · `python manage.py check` · `python manage.py test`
- `python manage.py makemigrations --check --dry-run`

Docker (Postgres/Redis/MinIO são stacks externas na rede `network_public`, que precisa existir):
- Dev: `docker compose up` (override: runserver, DEBUG, bind mount)
- Após mudar tasks: `docker compose restart worker beat`
- Comandos pontuais: `docker compose run --rm web python manage.py <comando>`
- Produção: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`

Não há linter, formatter, pytest nem CI. Não invente nem instale ferramentas; siga PEP 8 com 4 espaços. `setup/urls.py` usa 3 espaços e aspas simples: não reformate arquivos sem pedido.

## Armadilhas
- Sem `SECRET_KEY` no ambiente, qualquer `manage.py` falha no import (`ImproperlyConfigured`).
- O entrypoint NÃO roda migrate/collectstatic; migrations são aplicadas manualmente (skill `migration`).
- `CompressedManifestStaticFilesStorage`: com DEBUG=False, `{% static %}` de arquivo inexistente → erro 500.
- Imagem: `pip install --only-binary=:all:` (nova lib precisa de wheel); código root-owned, graváveis só `/app/media` e `/data/celerybeat`.
- `beat` deve ter uma única instância. O healthcheck do web exige `127.0.0.1` em `DJANGO_ALLOWED_HOSTS`.
- `USE_TZ=True` + `America/Sao_Paulo`: use `django.utils.timezone.now()`, nunca `datetime.now()`.
- `DjangoModelPermissions` (global) não restringe leitura nem isola usuários; `django_filters` não está em `DEFAULT_FILTER_BACKENDS`. Detalhes em `.claude/rules/api.md`.

## Segurança — requisito permanente de toda tarefa
1. Antes de implementar, identifique dados envolvidos, quem pode acessar e quais controles existentes se aplicam. Profundidade proporcional à sensibilidade.
2. Nunca leia nem exponha valores de `.env`, credenciais ou tokens (código, logs, docs, fixtures, testes, respostas). Para checar config, verifique só a presença da chave.
3. Não enfraqueça controles existentes: `DEBUG` default False, `SECRET_KEY` obrigatória, `ALLOWED_HOSTS` restrito, middlewares Security/CSRF/XFrame, validadores de senha, `DjangoModelPermissions` default, `USER appuser` no Dockerfile, portas em `127.0.0.1`, exclusões de `.env` no `.gitignore`/`.dockerignore`. Alterar exige justificativa e aprovação explícita.
4. Autorização sempre no backend e por objeto quando houver dono; ocultar elemento no template não é controle de acesso.
5. Toda entrada externa é não confiável (request, query param, upload, payload de task, arquivo): valide com serializer/form; use o ORM; SQL cru só parametrizado.
6. Nova dependência ou serviço externo: justifique, fixe a versão com `==`, confirme wheel e peça aprovação.
7. Operações destrutivas (`migrate` em banco compartilhado, `flush`, drop, `docker compose down -v`, remoção de volumes) e mudanças de permissões/config de segurança: só com aprovação explícita.
8. Ao concluir, informe verificações feitas, riscos remanescentes e limitações, distinguindo vulnerabilidade comprovada, risco potencial e recomendação. Ausência de achados não garante segurança.

## Mais contexto
- Regras por camada em `.claude/rules/` carregam automaticamente pelo caminho do arquivo.
- Skills: `nova-app`, `novo-endpoint`, `migration`, `pagina-html` (telas/templates), `estilos-css` (CSS), `codigo-js` (JavaScript), `validar-entrega` (sempre ao concluir).
- Auditoria independente: agent `security-auditor`; revisão de diff: `/code-review`, `/security-review`.
