"""Install the agent-tasks LaunchAgent on the Mac after manual review (every 5 minutes).

launchd runs ``scripts/run_agent_tasks.py`` every 300 seconds.  An interval missed while the Mac slept
fires once on wake, and the runner itself collapses every missed slot of a task into one run, so a task
due during sleep runs once after waking.  This writes no secret: the runner reads ``.env`` and the
encrypted background model at run time.

    python scripts/install_agent_tasks_launchagent.py --print   # show the plist, install nothing
    python scripts/install_agent_tasks_launchagent.py           # install (macOS only)
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.market-evidence-agent.agent-tasks"
INTERVAL_SECONDS = 300


def _rag_enabled(project_root: Path) -> bool:
    flag = os.environ.get("RAG_ENABLED", "")
    env_file = project_root / ".env"
    if not flag and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "RAG_ENABLED":
                flag = value.split("#", 1)[0].strip().strip("'\"")
    return flag.lower() in {"1", "true", "yes"}


def runner_python(project_root: Path) -> Path:
    """The interpreter the API uses (scripts/run_api_server.sh): MARKET_EVIDENCE_API_PYTHON, else .venv-rag
    when RAG_ENABLED, else .venv.  The runner needs the same packages (langgraph) as the API."""
    override = os.environ.get("MARKET_EVIDENCE_API_PYTHON")
    if override:
        path = Path(override)
        return path if path.is_absolute() else project_root / path
    return project_root / (".venv-rag" if _rag_enabled(project_root) else ".venv") / "bin" / "python"


def plist(project_root: Path) -> dict:
    log_directory = project_root / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [str(runner_python(project_root)), str(project_root / "scripts" / "run_agent_tasks.py")],
        "WorkingDirectory": str(project_root),
        "StartInterval": INTERVAL_SECONDS,
        # One run right after login/boot picks up whatever came due while the Mac was off.
        "RunAtLoad": True,
        "StandardOutPath": str(log_directory / "agent-tasks.out.log"),
        "StandardErrorPath": str(log_directory / "agent-tasks.err.log"),
        "ProcessType": "Background",
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", action="store_true", dest="print_only", help="print the plist without installing it")
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parent.parent
    payload = plist(project_root)
    rendered = plistlib.dumps(payload, sort_keys=False)
    if args.print_only:
        print(rendered.decode())
        return
    if sys.platform != "darwin":
        parser.error("launchd is macOS only; use --print to review the plist")
    python = Path(payload["ProgramArguments"][0])
    if not python.is_file():
        parser.error(f"project virtualenv is missing: {python}")
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / f"{LABEL}.plist"
    launch_agents.mkdir(parents=True, exist_ok=True)
    (project_root / "logs").mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(rendered)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    print(f"Installed {LABEL} (every {INTERVAL_SECONDS}s). Logs: {project_root / 'logs'}")


if __name__ == "__main__":
    main()
