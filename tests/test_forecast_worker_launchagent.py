import plistlib
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "scripts" / "run_forecast_worker.sh"
INSTALLER = PROJECT_ROOT / "scripts" / "install_forecast_worker_launchagent.py"


def test_forecast_worker_launcher_print_is_a_non_mutating_review_path():
    result = subprocess.run(
        ["/bin/sh", str(LAUNCHER), "--print"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert ".venv/bin/python -m app.forecast_worker --poll-seconds 2" in result.stdout
    assert "market-evidence-postgres" in result.stdout
    assert "Dry run only" in result.stdout


def test_worker_launchagent_prints_a_persistent_but_uninstalled_plist():
    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--print"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    payload = plistlib.loads(result.stdout.encode())
    assert payload["Label"] == "com.market-evidence-agent.forecast-worker"
    assert payload["ProgramArguments"] == ["/bin/sh", str(LAUNCHER)]
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["WorkingDirectory"] == str(PROJECT_ROOT)
