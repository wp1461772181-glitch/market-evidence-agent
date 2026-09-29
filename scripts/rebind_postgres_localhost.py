"""Recreate the existing Postgres container with a loopback-only host port.

The default is a read-only plan. ``--apply`` gracefully stops and renames the
current container, then starts a replacement that reuses the exact same Docker
volume. The old container is kept stopped for rollback; this script never
removes containers or volumes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime


CONTAINER = "market-evidence-postgres"
DATA_DIRECTORY = "/var/lib/postgresql/data"
HOST_PORT = "55432"
CONTAINER_PORT = "5432"


def docker(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *arguments], text=True, capture_output=True, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(f"docker {' '.join(arguments[:2])} failed")
    return result.stdout.strip()


def inspect_value(template: str, name: str = CONTAINER) -> str:
    return docker("inspect", "--format", template, name)


def inspect_container(name: str) -> bool:
    return subprocess.run(
        ["docker", "inspect", name], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def wait_until_ready(name: str, timeout_seconds: int = 45) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", name, "pg_isready", "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode == 0:
            return True
        time.sleep(1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the loopback-only port rebind")
    args = parser.parse_args()

    if not inspect_container(CONTAINER):
        parser.error(f"required existing container {CONTAINER} was not found")

    state = inspect_value("{{.State.Status}}")
    image = inspect_value("{{.Config.Image}}")
    mounts = json.loads(inspect_value("{{json .Mounts}}"))
    ports = json.loads(inspect_value("{{json .HostConfig.PortBindings}}"))
    restart_policy = inspect_value("{{.HostConfig.RestartPolicy.Name}}") or "no"

    data_mounts = [mount for mount in mounts if mount.get("Destination") == DATA_DIRECTORY]
    if len(data_mounts) != 1 or data_mounts[0].get("Type") != "volume":
        parser.error("expected one Docker-managed volume mounted at PostgreSQL's data directory")
    if not image.startswith("postgres:"):
        parser.error("refusing to recreate an unexpected database image")

    current_bindings = ports.get(f"{CONTAINER_PORT}/tcp") or []
    matching = [item for item in current_bindings if item.get("HostPort") == HOST_PORT]
    if not matching:
        parser.error(f"expected to find the existing host port {HOST_PORT} mapping")
    if any(item.get("HostIp") == "127.0.0.1" for item in matching):
        print(f"{CONTAINER} already publishes {HOST_PORT} on 127.0.0.1 only.")
        return 0

    backup = f"{CONTAINER}-before-localhost-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    if inspect_container(backup):
        parser.error(f"rollback container name already exists: {backup}")

    print(f"Current state: {state}; current host binding: all interfaces on port {HOST_PORT}.")
    print(f"Data volume: reused in place; image: {image}; restart policy: {restart_policy}.")
    print(f"Replacement binding: 127.0.0.1:{HOST_PORT} -> container port {CONTAINER_PORT}.")
    print(f"Rollback container retained as: {backup}.")
    if not args.apply:
        print("Dry run only. No container or database state was changed; pass --apply to proceed.")
        return 0
    if state != "running":
        parser.error("the existing database container must be running before applying this rebind")

    renamed = False
    try:
        docker("stop", "--time", "30", CONTAINER)
        docker("rename", CONTAINER, backup)
        renamed = True
        docker(
            "run", "--detach", "--name", CONTAINER,
            "--publish", f"127.0.0.1:{HOST_PORT}:{CONTAINER_PORT}",
            "--volumes-from", backup,
            "--restart", restart_policy,
            image,
        )
        if not wait_until_ready(CONTAINER):
            raise RuntimeError("replacement PostgreSQL did not become ready within 45 seconds")

        new_mounts = json.loads(inspect_value("{{json .Mounts}}"))
        new_data_mounts = [mount for mount in new_mounts if mount.get("Destination") == DATA_DIRECTORY]
        if len(new_data_mounts) != 1 or new_data_mounts[0].get("Name") != data_mounts[0].get("Name"):
            raise RuntimeError("replacement did not attach the original PostgreSQL data volume")
        new_ports = json.loads(inspect_value("{{json .HostConfig.PortBindings}}"))
        new_bindings = new_ports.get(f"{CONTAINER_PORT}/tcp") or []
        if new_bindings != [{"HostIp": "127.0.0.1", "HostPort": HOST_PORT}]:
            raise RuntimeError("replacement host binding was not limited to 127.0.0.1")
    except Exception as error:
        if renamed and inspect_container(CONTAINER):
            docker("stop", "--time", "20", CONTAINER, check=False)
            docker("rm", CONTAINER, check=False)  # Deliberately omit -v; retain the original data volume.
        if renamed and inspect_container(backup):
            docker("rename", backup, CONTAINER)
            docker("start", CONTAINER)
        elif inspect_container(CONTAINER) and inspect_value("{{.State.Running}}") != "true":
            docker("start", CONTAINER)
        print(f"Rebind failed; rollback attempted: {error}", file=sys.stderr)
        return 1

    print(f"Rebind complete: port {HOST_PORT} now listens on 127.0.0.1 only.")
    print("The existing data volume is attached unchanged; the old container remains stopped for rollback.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
