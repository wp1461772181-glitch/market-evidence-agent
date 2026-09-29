"""Print or install a persistent local API LaunchAgent.

The API reads database and provider settings from the ignored local ``.env``.
This installer does not read or write credentials.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


LABEL = "com.market-evidence-agent.api"


def build_payload(project_root: Path) -> dict[str, object]:
    launcher = project_root / "scripts" / "run_api_server.sh"
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(launcher)],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(project_root / "logs" / "api-server.out.log"),
        "StandardErrorPath": str(project_root / "logs" / "api-server.err.log"),
        "ProcessType": "Background",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", dest="print_only", help="print the plist without installing it")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    launcher = project_root / "scripts" / "run_api_server.sh"
    if not launcher.is_file():
        parser.error(f"API launcher is missing: {launcher}")
    rendered = plistlib.dumps(build_payload(project_root), sort_keys=False)
    if args.print_only:
        print(rendered.decode())
        return

    python = project_root / ".venv" / "bin" / "python"
    if not python.is_file():
        parser.error(f"project virtualenv is missing: {python}")
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
