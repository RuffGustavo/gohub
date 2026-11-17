---
paths:
  - "**/tasks.py"
  - "**/tasks/**/*.py"
  - "setup/celery.py"
---
# Tarefas Celery

- Tasks em `<app>/tasks.py` com `@shared_task` (descobertas por `autodiscover_tasks`).
- Argumentos mínimos e serializáveis em JSON: passe IDs, nunca instâncias de model, segredos ou dados pessoais (args ficam no Redis e nos logs do worker).
- Idempotente: pode rodar mais de uma vez (retry/redelivery). Recarregue o objeto pelo ID e cheque o estado antes de agir.
- Falhas transitórias: `autoretry_for`, `retry_backoff`, `max_retries` explícitos; não engula exceções.
- Disparo após gravar no banco: `transaction.on_commit(lambda: minha_task.delay(obj.pk))`.
- Não há result backend: não use `.get()`/`AsyncResult` sem aprovar a configuração de um.
- Periódicas: `CELERY_BEAT_SCHEDULE` em `setup/settings.py`; beat é instância única.
- Worker/beat não recarregam: `docker compose restart worker beat`.
