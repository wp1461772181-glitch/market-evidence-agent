#!/bin/sh
# Start the local API after waiting for the existing PostgreSQL container.
# This script never creates, resets, or deletes a database or container.
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CONTAINER_NAME="market-evidence-postgres"
DATABASE_NAME="market_evidence"
DATABASE_USER="market_evidence"
MAX_WAIT_SECONDS="${MARKET_EVIDENCE_API_WAIT_SECONDS:-90}"
API_PYTHON="${MARKET_EVIDENCE_API_PYTHON:-${PROJECT_ROOT}/.venv/bin/python}"
case "$API_PYTHON" in /*) ;; *) API_PYTHON="${PROJECT_ROOT}/${API_PYTHON#./}" ;; esac
RUNTIME_ROOT="${PROJECT_ROOT}/data/releases/current"
if [ ! -d "$RUNTIME_ROOT" ]; then RUNTIME_ROOT="$PROJECT_ROOT"; fi
if [ -z "${LOCAL_MODEL_PROFILES_PATH:-}" ]; then LOCAL_MODEL_PROFILES_PATH="$PROJECT_ROOT/data/model-profiles.json"; fi
export LOCAL_MODEL_PROFILES_PATH
if [ -z "${PERSONAL_CHAT_LOGIN_PATH:-}" ]; then PERSONAL_CHAT_LOGIN_PATH="$PROJECT_ROOT/data/personal-chat-login.json"; fi
export PERSONAL_CHAT_LOGIN_PATH
PATH="${HOME:-}/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:/Applications/OrbStack.app/Contents/MacOS/bin:/usr/bin:/bin"
export PATH

if [ "${1:-}" = "--print" ]; then
    if [ -n "${MARKET_EVIDENCE_API_PYTHON:-}" ] && [ ! -x "$API_PYTHON" ]; then
        printf '%s\n' "Configured API Python is not executable: ${API_PYTHON}" >&2
        exit 1
    fi
    printf '%s\n' "API server: ${API_PYTHON} -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    printf '%s\n' "Runtime source: ${RUNTIME_ROOT}; env file: ${PROJECT_ROOT}/.env"
    printf '%s\n' "Required existing container: ${CONTAINER_NAME} (${DATABASE_USER}/${DATABASE_NAME})"
    printf '%s\n' "Database binding must remain on 127.0.0.1:55432."
    printf '%s\n' "Dry run only: Docker and service state were not changed."
    exit 0
fi

if [ "$#" -ne 0 ]; then
    printf '%s\n' "Usage: $0 [--print]" >&2
    exit 2
fi
if [ ! -x "$API_PYTHON" ]; then
    printf '%s\n' "Configured API Python is not executable: ${API_PYTHON}" >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' "Docker CLI was not found in launchd PATH: ${PATH}" >&2
    exit 1
fi
DOCKER_BIN=$(command -v docker)

if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
    printf '%s\n' "Docker is unavailable; opening OrbStack and waiting up to ${MAX_WAIT_SECONDS}s."
    /usr/bin/open -gj -a OrbStack
    waited=0
    until "$DOCKER_BIN" info >/dev/null 2>&1; do
        waited=$((waited + 1))
        if [ "$waited" -ge "$MAX_WAIT_SECONDS" ]; then
            printf '%s\n' "Docker/OrbStack did not become ready after ${MAX_WAIT_SECONDS}s." >&2
            exit 1
        fi
        /bin/sleep 1
    done
fi
if ! "$DOCKER_BIN" inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    printf '%s\n' "Required existing container ${CONTAINER_NAME} does not exist; refusing to create one." >&2
    exit 1
fi
if [ "$("$DOCKER_BIN" inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" != "true" ]; then
    printf '%s\n' "Starting existing container ${CONTAINER_NAME}."
    "$DOCKER_BIN" start "$CONTAINER_NAME" >/dev/null
fi

waited=0
until "$DOCKER_BIN" exec "$CONTAINER_NAME" pg_isready -U "$DATABASE_USER" -d "$DATABASE_NAME" >/dev/null 2>&1; do
    waited=$((waited + 1))
    if [ "$waited" -ge "$MAX_WAIT_SECONDS" ]; then
        printf '%s\n' "PostgreSQL in ${CONTAINER_NAME} did not become ready after ${MAX_WAIT_SECONDS}s." >&2
        exit 1
    fi
    /bin/sleep 1
done

printf '%s\n' "PostgreSQL is ready; starting the loopback-only API."
exec "$API_PYTHON" -m uvicorn app.main:app --app-dir "$RUNTIME_ROOT" --env-file "$PROJECT_ROOT/.env" --host 127.0.0.1 --port 8000
