#!/bin/sh
# Prepare the existing local PostgreSQL dependency before a launchd run.
# This intentionally never creates, resets, or restarts a container.
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CONTAINER_NAME="market-evidence-postgres"
DATABASE_NAME="market_evidence"
DATABASE_USER="market_evidence"
MAX_WAIT_SECONDS="${MARKET_EVIDENCE_MONITOR_WAIT_SECONDS:-60}"

# launchd has a deliberately small PATH. OrbStack's per-user CLI location is
# included as well as the common Homebrew and Docker Desktop locations.
PATH="${HOME:-}/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:/Applications/OrbStack.app/Contents/MacOS/bin:/usr/bin:/bin"
export PATH

if [ "${1:-}" = "--print" ]; then
    printf '%s\n' "Monitor launcher: ${PROJECT_ROOT}/.venv/bin/python -m app.official_monitor"
    printf '%s\n' "Required existing container: ${CONTAINER_NAME} (${DATABASE_USER}/${DATABASE_NAME})"
    printf '%s\n' "PATH: ${PATH}"
    printf '%s\n' "Dry run only: OrbStack and Docker state were not changed."
    exit 0
fi

if [ "$#" -ne 0 ]; then
    printf '%s\n' "Usage: $0 [--print]" >&2
    exit 2
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

printf '%s\n' "PostgreSQL is ready; starting the SEC monitor."
exec "${PROJECT_ROOT}/.venv/bin/python" -m app.official_monitor
