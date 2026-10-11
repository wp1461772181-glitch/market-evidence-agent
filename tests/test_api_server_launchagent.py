import os
import plistlib
import shutil
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

    assert f"{PROJECT_ROOT}/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000" in result.stdout
    assert "market-evidence-postgres" in result.stdout
    assert "Dry run only" in result.stdout


def test_fresh_checkout_default_print_does_not_require_dependency_directories(tmp_path):
    project = tmp_path / "fresh-checkout"
    (project / "scripts").mkdir(parents=True)
    launcher = project / "scripts" / "run_api_server.sh"
    shutil.copy2(LAUNCHER, launcher)

    result = subprocess.run(
        ["/bin/sh", str(launcher), "--print"],
        cwd=project,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"{project}/.venv/bin/python -m uvicorn" in result.stdout
    assert not (project / ".venv").exists()
    assert not (project / ".venv-rag").exists()


def test_api_launcher_print_accepts_explicit_executable_override():
    environment = os.environ.copy()
    environment["MARKET_EVIDENCE_API_PYTHON"] = sys.executable
    result = subprocess.run(
        ["/bin/sh", str(LAUNCHER), "--print"],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"API server: {sys.executable} -m uvicorn" in result.stdout


def test_api_launcher_rejects_missing_interpreter_for_runtime_and_explicit_print():
    environment = os.environ.copy()
    environment["MARKET_EVIDENCE_API_PYTHON"] = "/missing/test/python"
    for arguments in ([], ["--print"]):
        result = subprocess.run(
            ["/bin/sh", str(LAUNCHER), *arguments],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 1
        assert "not executable" in result.stderr


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


def test_api_launcher_print_accepts_explicit_executable_runtime_without_starting_services():
    override = PROJECT_ROOT / ".venv-rag" / "bin" / "python"
    result = subprocess.run(
        ["/bin/sh", str(LAUNCHER), "--print"],
        cwd=PROJECT_ROOT,
        env={**__import__("os").environ, "MARKET_EVIDENCE_API_PYTHON": str(override)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"API server: {override} -m uvicorn" in result.stdout
    assert "Dry run only" in result.stdout


def test_api_launcher_print_rejects_non_executable_runtime():
    result = subprocess.run(
        ["/bin/sh", str(LAUNCHER), "--print"],
        cwd=PROJECT_ROOT,
        env={**__import__("os").environ, "MARKET_EVIDENCE_API_PYTHON": "/does/not/exist/python"},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "not executable" in result.stderr
