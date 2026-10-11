#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DB_NAME=${CH34_TEST_DATABASE_NAME:-test_market_rag_ch34_demo}
case "$DB_NAME" in test_*) ;; *) echo 'CH34_TEST_DATABASE_NAME must begin with test_' >&2; exit 2;; esac
case "$DB_NAME" in *[!a-z0-9_]*) echo 'CH34_TEST_DATABASE_NAME may contain only lowercase letters, digits, and underscores.' >&2; exit 2;; esac
ADMIN_DSN=${CH34_TEST_ADMIN_DSN:-}
if [ -z "$ADMIN_DSN" ]; then echo 'Set CH34_TEST_ADMIN_DSN to the local PostgreSQL admin DSN (database postgres).' >&2; exit 2; fi
TEST_DSN=${CH34_TEST_DATABASE_URL:-}
if [ -z "$TEST_DSN" ]; then echo 'Set CH34_TEST_DATABASE_URL to the local test database URL.' >&2; exit 2; fi
DEMO_OPERATION_KEY=${CH34_TEST_OPERATION_KEY:-}
if [ "${#DEMO_OPERATION_KEY}" -lt 32 ]; then echo 'Set CH34_TEST_OPERATION_KEY to a separate test-only operation key of at least 32 characters.' >&2; exit 2; fi
if lsof -nP -iTCP:8018 -sTCP:LISTEN >/dev/null 2>&1 || lsof -nP -iTCP:5180 -sTCP:LISTEN >/dev/null 2>&1; then
  echo 'Ports 8018 or 5180 already have listeners; refusing to replace them.' >&2; exit 2
fi
cd "$ROOT"
export CH34_TEST_DATABASE_NAME="$DB_NAME"
.venv-rag/bin/python - <<'PY'
import sys
import psycopg
import os
from sqlalchemy.engine import make_url
name=os.environ['CH34_TEST_DATABASE_NAME']
admin=make_url(os.environ['CH34_TEST_ADMIN_DSN'])
if admin.get_backend_name()!='postgresql' or admin.database!='postgres' or admin.host not in ('127.0.0.1','localhost','::1') or admin.port!=55432:
    raise SystemExit('CH34_TEST_ADMIN_DSN must target the local PostgreSQL postgres database on port 55432')
target=make_url(os.environ['CH34_TEST_DATABASE_URL'])
if target.get_backend_name()!='postgresql' or target.database!=name or target.host not in ('127.0.0.1','localhost','::1') or target.port!=55432:
    raise SystemExit('CH34_TEST_DATABASE_URL must target the selected local test_* database on port 55432')
with psycopg.connect(admin.render_as_string(hide_password=False),autocommit=True) as conn:
    exists=conn.execute('select 1 from pg_database where datname=%s',(name,)).fetchone()
    if not exists:
        conn.execute(f'CREATE DATABASE "{name}"')
PY
export DATABASE_URL="$TEST_DSN"
export TEST_DATABASE_URL="$TEST_DSN"
export APP_API_ACCESS_KEY="$DEMO_OPERATION_KEY"
export PUBLIC_API_MODE=false
export RAG_ENABLED=true
export RAG_INDEXING_ENABLED=true
export RAG_RETRIEVAL_STRATEGY=hybrid_rerank
export RAG_MODEL_PATH="$ROOT/data/knowledge/models/bge-m3"
export RAG_RERANKER_PATH="$ROOT/data/knowledge/models/bge-reranker-v2-m3"
PROFILE_PATH=/tmp/market-rag-ch34-20261011/local-model-profiles.json
mkdir -p "$(dirname "$PROFILE_PATH")"
if [ "${CH34_ENABLE_SIMULATED_PROVIDER:-false}" = true ]; then
  cat > "$PROFILE_PATH" <<'JSON'
{"version":1,"profiles":[{"id":"ch34-visual-demo","display_name":"Chapter 3/4 visual demo (simulated; requests will fail closed)","provider":"custom","base_url":"https://provider.invalid/v1","model":"ch34-simulated","api_key":"fake-test-key-not-a-credential","tested_at":null}],"selected_id":"ch34-visual-demo"}
JSON
else
  printf '{"version":1,"profiles":[],"selected_id":null}\n' > "$PROFILE_PATH"
fi
export LOCAL_MODEL_PROFILES_ENABLED=true
export LOCAL_MODEL_PROFILES_PATH="$PROFILE_PATH"
export VITE_API_PROXY_TARGET=http://127.0.0.1:8018
.venv-rag/bin/python scripts/migrate_knowledge.py
.venv-rag/bin/python scripts/seed_ch34_demo.py
.venv-rag/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8018 &
API_PID=$!
(
  cd frontend
  npm run dev -- --host 127.0.0.1 --port 5180
) &
WEB_PID=$!
cleanup(){ kill "$API_PID" "$WEB_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
printf 'Chapter 3/4 isolated local review\nAPI: http://127.0.0.1:8018\nWeb: http://127.0.0.1:5180\nDatabase: %s (test-only)\nRAG strategy: hybrid_rerank; model profiles: %s\n' "$DB_NAME" "$PROFILE_PATH"
wait "$API_PID"
wait "$WEB_PID"
