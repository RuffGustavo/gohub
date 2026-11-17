---
paths:
  - "Dockerfile"
  - "docker-compose*.yml"
  - "entrypoint.sh"
  - "gunicorn.conf.py"
  - "requirements.txt"
  - ".env.example"
  - ".dockerignore"
  - ".gitignore"
  - "setup/settings.py"
  - "setup/celery.py"
  - "setup/wsgi.py"
  - "setup/asgi.py"
---
# Infraestrutura e configuração

## Settings
- Ler de env com `os.getenv`/`env_bool`/`env_list`; default sempre o mais restritivo. Segredo obrigatório falha cedo com `ImproperlyConfigured` (padrão de `SECRET_KEY`).
- Toda nova chave em `.env.example` com valor fictício e comentário em português. Nunca copie valores do `.env`.
- Proibido sem aprovação: `DEBUG` default True, `ALLOWED_HOSTS=["*"]`, remover/reordenar middlewares de segurança, CORS aberto, segredo hardcoded, desligar validadores de senha.
- HTTPS/cookies (`SECURE_*`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, HSTS) ainda não existem e dependem do proxy reverso: proponha via env, não aplique sem aprovação (HSTS é difícil de reverter).

## Docker / compose / entrypoint
- Dockerfile: manter multi-stage, `--only-binary=:all:`, `USER appuser`, código root-owned; diretório gravável novo via `install -d -o appuser -g appuser`. Sem compiladores, `sudo`, `chmod 777`. A `SECRET_KEY` do `collectstatic` é exclusiva daquele `RUN`: não replique o padrão para runtime.
- Compose: portas só em `127.0.0.1:`; Postgres/Redis/MinIO são externos (`network_public`) — não adicione esses serviços nem `depends_on` para eles; use `WAIT_FOR`.
- `entrypoint.sh`: só espera dependências e faz `exec`; não adicione migrate/collectstatic/criação de usuário; mantenha `set -Eeuo pipefail` e argumentos posicionais em `bash -c`.
- Não remova `.env`/`.env.*` do `.gitignore`/`.dockerignore`.

## Dependências
- `requirements.txt` em formato `pip freeze` (`==`, ordem alfabética). Nova lib: necessidade justificada, wheel para Python 3.13, aprovação. Upgrade: ler changelog/avisos de segurança.

## Validação
- `DJANGO_DEBUG=false python manage.py check --deploy`: não pode surgir aviso além dos conhecidos (W004, W008, W012, W016; W009 só com o `.env` local).
