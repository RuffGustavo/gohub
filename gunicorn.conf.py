"""Configuração do Gunicorn (lida automaticamente a partir do diretório de trabalho)."""

import os

bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.getenv("GUNICORN_WORKERS", "3"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# Logs para stdout/stderr.
accesslog = "-"
errorlog = "-"

# Heartbeat dos workers em memória (evita I/O em disco do overlay do container).
worker_tmp_dir = "/dev/shm"
