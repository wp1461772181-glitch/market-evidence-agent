import os
import subprocess
import sys
from pathlib import Path


def test_reconcile_cli_rejects_non_test_database_before_app_connects():
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,'DATABASE_URL':'postgresql+psycopg://x:x@127.0.0.1:55432/market_evidence'}
    result=subprocess.run([sys.executable,'scripts/reconcile_knowledge.py'],cwd=root,env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode!=0
    assert 'must target a PostgreSQL test_* database' in result.stderr
    assert 'Traceback' not in result.stderr
