import plistlib
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = PROJECT_ROOT / "scripts" / "install_api_launchagent.py"
LAUNCHER = PROJECT_ROOT / "scripts" / "run_api_server.sh"


def test_api_launcher_print_is_a_non_mutating_review_path():
    result = subprocess.run(
        ["/bin/sh", str(LAUNCHER), "--print"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert ".venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000" in result.stdout
    assert "market-evidence-postgres" in result.stdout
    assert "Dry run only" in result.stdout


def test_api_launchagent_prints_a_loopback_only_persistent_plist():
    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--print"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    payload = plistlib.loads(result.stdout.encode())
    assert payload["Label"] == "com.market-evidence-agent.api"
    assert payload["ProgramArguments"] == ["/bin/sh", str(LAUNCHER)]
    assert payload["WorkingDirectory"] == str(PROJECT_ROOT)
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
