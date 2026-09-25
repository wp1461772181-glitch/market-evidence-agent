"""Install the local, hourly SEC monitor LaunchAgent after manual review.

This script is intentionally not run during development. It writes no secret:
the monitored command reads the project's ignored ``.env`` only at runtime.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


LABEL = "com.market-evidence-agent.official-sec-monitor"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", dest="print_only", help="print the plist without installing it")
    parser.add_argument("--mode", choices=("legacy", "v2"), default="legacy", help="monitor implementation to run")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    python = project_root / ".venv" / "bin" / "python"
    if not python.is_file():
        parser.error(f"project virtualenv is missing: {python}")
    launcher = project_root / "scripts" / "run_official_monitor.sh"
    if not launcher.is_file():
        parser.error(f"monitor launcher is missing: {launcher}")
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / f"{LABEL}.plist"
    log_directory = project_root / "logs"
    payload = {
        "Label": LABEL,
        # The wrapper makes the launchd environment robust after a laptop wakes:
        # it starts only the existing development container and waits for Postgres.
        "ProgramArguments": ["/bin/sh", str(launcher)],
        "WorkingDirectory": str(project_root),
        "StartInterval": 3600,
        "RunAtLoad": False,
        "EnvironmentVariables": {"OFFICIAL_MONITOR_MODE": args.mode},
        "StandardOutPath": str(log_directory / "official-sec-monitor.out.log"),
        "StandardErrorPath": str(log_directory / "official-sec-monitor.err.log"),
        "ProcessType": "Background",
    }
    rendered = plistlib.dumps(payload, sort_keys=False)
    if args.print_only:
        print(f"OFFICIAL_MONITOR_MODE={args.mode}")
        print(rendered.decode())
        return

    launch_agents.mkdir(parents=True, exist_ok=True)
    log_directory.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(rendered)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    print(f"Installed {LABEL} in {args.mode} mode. Logs: {log_directory}")


if __name__ == "__main__":
    main()
