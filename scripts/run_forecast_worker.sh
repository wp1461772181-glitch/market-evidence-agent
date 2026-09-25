#!/bin/sh
# Run the durable V2 forecast worker from launchd or a local shell.
#
# The wrapper only starts an existing development database container. It never
# creates, resets, or deletes data, so an accidental background launch cannot
# silently replace a developer's database.
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CONTAINER_NAME="market-evidence-postgres"
DATABASE_NAME="market_evidence"
DATABASE_USER="market_evidence"
MAX_WAIT_SECONDS="${MARKET_EVIDENCE_WORKER_WAIT_SECONDS:-60}"
POLL_SECONDS="${FORECAST_WORKER_POLL_SECONDS:-2}"

# launchd provides a deliberately small PATH. Include the local container
# clients that are commonly installed on this machine before checking Docker.
PATH="${HOME:-}/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:/Applications/OrbStack.app/Contents/MacOS/bin:/usr/bin:/bin"
export PATH

if [ "${1:-}" = "--print" ]; then
    printf '%s\n' "Forecast worker launcher: ${PROJECT_ROOT}/.venv/bin/python -m app.forecast_worker --poll-seconds ${POLL_SECONDS}"
    printf '%s\n' "Required existing container: ${CONTAINER_NAME} (${DATABASE_USER}/${DATABASE_NAME})"
    printf '%s\n' "PATH: ${PATH}"
    printf '%s\n' "Dry run only: OrbStack and Docker state were not changed."
    exit 0
fi

if [ "$#" -ne 0 ]; then
    printf '%s\n' "Usage: $0 [--print]" >&2
    exit 2
fi

if [ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    printf '%s\n' "Project virtualenv Python is missing: ${PROJECT_ROOT}/.venv/bin/python" >&2
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

printf '%s\n' "PostgreSQL is ready; starting the forecast worker."
exec "${PROJECT_ROOT}/.venv/bin/python" -m app.forecast_worker --poll-seconds "$POLL_SECONDS"
