#!/usr/bin/env bash
# Ponto de entrada comum aos serviços web, worker e beat.
#
# Responsabilidades (apenas):
#   1. Opcionalmente aguardar dependências TCP listadas em WAIT_FOR
#      (ex.: WAIT_FOR="postgres:5432 redis:6379"), com timeout.
#   2. Substituir-se pelo processo principal via exec (sinais vão direto a ele).
#
# NÃO executa migrate, collectstatic nem cria usuários: essas operações são
# comandos pontuais (docker compose run --rm web python manage.py ...).
set -Eeuo pipefail

log() { printf '%s [entrypoint] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() { log "ERRO: $*"; exit 1; }
trap 'die "falha inesperada na linha ${LINENO}"' ERR

WAIT_FOR="${WAIT_FOR:-}"
WAIT_FOR_TIMEOUT="${WAIT_FOR_TIMEOUT:-60}"

[[ "$WAIT_FOR_TIMEOUT" =~ ^[0-9]+$ ]] || die "WAIT_FOR_TIMEOUT inválido: '${WAIT_FOR_TIMEOUT}'"

# Verifica apenas se a porta aceita conexões TCP. Não substitui o healthcheck
# do próprio serviço (ex.: PING autenticado do Redis), nem a lógica de
# reconexão da aplicação.
wait_for() {
    local target="$1" host port deadline
    host="${target%:*}"
    port="${target##*:}"
    if [[ -z "$host" || "$host" == "$target" || ! "$port" =~ ^[0-9]+$ ]]; then
        die "entrada inválida em WAIT_FOR: '${target}' (formato esperado host:porta)"
    fi

    deadline=$((SECONDS + WAIT_FOR_TIMEOUT))
    log "aguardando ${host}:${port} (timeout ${WAIT_FOR_TIMEOUT}s)"
    # host/porta vão como argumentos posicionais ($1/$2) para o bash interno,
    # nunca interpolados no código (sem injeção). Aspas simples são intencionais.
    # shellcheck disable=SC2016
    until timeout 2 bash -c 'exec 3<>"/dev/tcp/$1/$2"' _ "$host" "$port" 2>/dev/null; do
        if ((SECONDS >= deadline)); then
            die "${host}:${port} não aceitou conexões em ${WAIT_FOR_TIMEOUT}s"
        fi
        sleep 1
    done
    log "${host}:${port} aceitando conexões"
}

[[ $# -gt 0 ]] || die "nenhum comando informado"

read -r -a targets <<<"$WAIT_FOR"
for target in "${targets[@]}"; do
    wait_for "$target"
done

log "iniciando: $*"
exec "$@"
