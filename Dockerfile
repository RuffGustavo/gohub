# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13.18-slim-trixie

# ---- Estágio 1: dependências Python isoladas em um venv ----
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Só wheels binários: todas as dependências atuais têm wheel, então não há
# compiladores na imagem. Se algum pacote exigir compilação, o build falha
# explicitamente em vez de puxar build-essential silenciosamente.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --only-binary=:all: -r /tmp/requirements.txt


# ---- Estágio 2: runtime ----
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=setup.settings

RUN useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0755 entrypoint.sh /entrypoint.sh

WORKDIR /app
# Código pertence ao root (somente leitura para o appuser).
COPY . .

# Estáticos coletados no build: imagem autocontida, sem volume nem concorrência.
# A SECRET_KEY abaixo é descartável, existe só neste RUN e não é usada em runtime.
# Diretórios graváveis: media (uploads) e o schedule do Celery Beat.
RUN SECRET_KEY=collectstatic-build-only python manage.py collectstatic --noinput \
 && install -d -o appuser -g appuser /app/media /data/celerybeat

USER appuser

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
# Configuração em gunicorn.conf.py (carregado automaticamente do diretório /app).
CMD ["gunicorn", "setup.wsgi:application"]
