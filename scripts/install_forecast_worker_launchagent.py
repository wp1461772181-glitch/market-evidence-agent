"""Print or install the local persistent V2 forecast worker LaunchAgent.

``--print`` is the default review path during development. Installation is a
separate, explicit operation because launchd keeps a background worker alive.
The worker obtains runtime configuration from the ignored project ``.env``;
this installer writes no credentials.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


LABEL = "com.market-evidence-agent.forecast-worker"


def build_payload(project_root: Path) -> dict[str, object]:
    launcher = project_root / "scripts" / "run_forecast_worker.sh"
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(launcher)],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        # A worker normally never exits. If a dependency failure makes it
        # exit non-zero, launchd restarts it after the dependency is available.
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(project_root / "logs" / "forecast-worker.out.log"),
        "StandardErrorPath": str(project_root / "logs" / "forecast-worker.err.log"),
        "ProcessType": "Background",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", dest="print_only", help="print the plist without installing it")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    python = project_root / ".venv" / "bin" / "python"
    if not python.is_file():
        parser.error(f"project virtualenv is missing: {python}")
    launcher = project_root / "scripts" / "run_forecast_worker.sh"
    if not launcher.is_file():
        parser.error(f"forecast worker launcher is missing: {launcher}")

    rendered = plistlib.dumps(build_payload(project_root), sort_keys=False)
    if args.print_only:
        print(rendered.decode())
        return

    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / f"{LABEL}.plist"
    log_directory = project_root / "logs"
    launch_agents.mkdir(parents=True, exist_ok=True)
    log_directory.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(rendered)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    print(f"Installed {LABEL}. Logs: {log_directory}")


if __name__ == "__main__":
    main()
