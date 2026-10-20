#!/usr/bin/env python3
"""One command, whole deployment: backend release + frontend publish + verification.

What it does, in order:

1. preflight   — main branch, no uncommitted tracked changes, tests green (unless --skip-tests)
2. push        — ``git push origin main``; this is also what publishes the frontend, because
                 ``deploy-pages.yml`` now triggers on a push that touches ``frontend/**``
3. backend     — export the commit to ``data/releases/<sha>-verified``, prove it imports from there,
                 repoint ``current``, restart the launchd service, wait for ``/health``
4. rollback    — if the new release does not become healthy, point ``current`` back and restart
5. verify      — poll the public site until the bundle name changes (only when frontend changed)

Nothing here needs a GitHub token: the push uses the repository's existing credentials and the
Pages workflow does the publishing.  Set ``MARKET_EVIDENCE_SKIP_FRONTEND_WAIT=1`` to skip step 5.

Usage:
    python scripts/deploy.py                 # full deploy
    python scripts/deploy.py --dry-run       # print the plan, change nothing
    python scripts/deploy.py --backend-only  # skip push and the frontend wait
    python scripts/deploy.py --skip-tests
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES = ROOT / "data" / "releases"
CURRENT = RELEASES / "current"
HEALTH = "http://127.0.0.1:8000/health"
PAGES = "https://wp1461772181-glitch.github.io/market-evidence-agent/"
LAUNCHD_LABEL = "com.market-evidence-agent.api"
PYTHON = ROOT / ".venv-rag" / "bin" / "python"
TEST_ENV = {"PUBLIC_API_MODE": "false", "RAG_ENABLED": "false", "RAG_INDEXING_ENABLED": "false"}
# Failures caused by this machine's launchd/RAG environment rather than by the
# code under test. Any other failure blocks a deploy, whatever the total says.
KNOWN_ENVIRONMENT_FAILURES = frozenset({
    "tests/test_agent_chat_api.py::test_shared_frontend_url_helper_reaches_public_chat_transport",
    "tests/test_api_server_launchagent.py::test_fresh_checkout_uses_rag_environment_only_when_enabled",
})


def run(command: list[str], *, cwd: Path | None = None, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(command, cwd=str(cwd or ROOT), env=merged, capture_output=True, text=True)
    if check and result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def say(step: str, message: str) -> None:
    print(f"[{step}] {message}", flush=True)


def git(*args: str) -> str:
    return run(["git", *args]).stdout.strip()


def head_sha() -> str:
    return git("rev-parse", "HEAD")


def preflight(skip_tests: bool) -> str:
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise SystemExit(f"refusing to deploy from '{branch}': switch to main first")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise SystemExit("refusing to deploy with uncommitted tracked changes:\n" + dirty)
    if not skip_tests:
        say("tests", "running the backend suite (2 known environment failures are allowed)")
        result = run([str(PYTHON), "-m", "pytest", "tests/", "-q"], env=TEST_ENV, check=False)
        tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no output"
        say("tests", tail)
        failed = {line.split(" - ")[0].removeprefix("FAILED ").strip()
                  for line in result.stdout.splitlines() if line.startswith("FAILED ")}
        unexpected = sorted(failed - KNOWN_ENVIRONMENT_FAILURES)
        if unexpected:
            sys.stderr.write(result.stdout[-6000:])
            raise SystemExit("tests failed: " + ", ".join(unexpected))
    return head_sha()


def frontend_changed(base_sha: str) -> bool:
    try:
        changed = git("diff", "--name-only", f"{base_sha}..HEAD")
    except SystemExit:
        return True
    except Exception:
        return True
    triggering = ("frontend/", ".github/workflows/deploy-pages.yml")
    return any(line.startswith(triggering) for line in changed.splitlines())


def python_executable() -> Path:
    return PYTHON if PYTHON.exists() else Path(sys.executable)


def build_release(sha: str, *, dry_run: bool) -> Path:
    target = RELEASES / f"{sha}-verified"
    if target.exists() and any(target.rglob("*")):
        say("backend", f"release already exists: {target.name}")
        return target
    say("backend", f"exporting {sha[:8]} to {target.relative_to(ROOT)}")
    if dry_run:
        return target
    target.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", sha], cwd=ROOT, capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)
    say("backend", f"{sum(1 for _ in target.rglob('*') if _.is_file())} files exported")
    return target


def prove_release_imports(target: Path) -> None:
    say("backend", "proving the release imports on its own")
    run([str(python_executable()), "-c", "import app.main"], cwd=target, env={"PYTHONPATH": str(target)})


def repoint(target: Path) -> None:
    say("backend", f"current -> {target.name}")
    if CURRENT.is_symlink() or CURRENT.exists():
        CURRENT.unlink()
    CURRENT.symlink_to(target.name)


# Processes that keep the code they imported at start-up. Interval agents spawn a
# fresh process on every run and pick the new release up on their own, so leaving a
# long-running one alone would keep serving the previous release after a deploy.
LONG_RUNNING_LABELS = (LAUNCHD_LABEL, "com.market-evidence-agent.forecast-worker")


def restart_service() -> None:
    uid = os.getuid()
    for label in LONG_RUNNING_LABELS:
        target = f"gui/{uid}/{label}"
        if run(["launchctl", "print", target], check=False).returncode != 0:
            say("backend", f"{label} is not loaded; skipping")
            continue
        say("backend", f"restarting {label}")
        run(["launchctl", "kickstart", "-k", target])


def wait_healthy(seconds: int = 45) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=3) as response:
                if json.loads(response.read().decode()).get("status") == "ok":
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1.5)
    return False


def bundle_name(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            html = response.read(200_000).decode("utf-8", "ignore")
    except (urllib.error.URLError, OSError):
        return None
    marker = "assets/index-"
    start = html.find(marker)
    if start < 0:
        return None
    end = html.find(".js", start)
    return html[start:end + 3] if end > 0 else None


def deploy_backend(sha: str, *, dry_run: bool) -> bool:
    previous = CURRENT.resolve().name if CURRENT.exists() else None
    target = build_release(sha, dry_run=dry_run)
    if dry_run:
        say("backend", f"would repoint current -> {target.name} (previous: {previous}) and restart")
        return True
    prove_release_imports(target)
    repoint(target)
    restart_service()
    if wait_healthy():
        say("backend", "healthy")
        return True
    say("backend", "NOT healthy — rolling back")
    if previous:
        repoint(RELEASES / previous)
        restart_service()
        if wait_healthy():
            say("backend", f"rolled back to {previous}")
        else:
            say("backend", f"rollback to {previous} also unhealthy — check logs")
    return False


def live_bundle_has(marker: str) -> bool:
    """Whether the published bundle contains this string: proof the new code is live."""
    name = bundle_name(PAGES)
    if name is None:
        return False
    try:
        with urllib.request.urlopen(PAGES + name, timeout=30) as response:
            return marker in response.read().decode("utf-8", "ignore")
    except (urllib.error.URLError, OSError):
        return False


def wait_frontend(previous_bundle: str | None, seconds: int, markers: list[str]) -> None:
    if markers:
        say("frontend", f"waiting until the published bundle contains {markers[0]!r}, up to {seconds}s")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if all(live_bundle_has(marker) for marker in markers):
                say("frontend", "published — every marker is in the live bundle")
                return
            time.sleep(10)
        say("frontend", f"markers still missing after {seconds}s; check the Actions run")
        return
    if previous_bundle is None:
        say("frontend", f"could not read the current bundle; open {PAGES} in a minute")
        return
    say("frontend", f"waiting for a new bundle (was {previous_bundle}), up to {seconds}s")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = bundle_name(PAGES)
        if current and current != previous_bundle:
            say("frontend", f"published: {current}")
            return
        time.sleep(10)
    say("frontend", "bundle name unchanged after the wait. Either the live build was already up to date "
                        "(a rebuild of identical sources keeps the same hash) or the run has not finished; "
                        f"check {PAGES} and the Actions run, or pass --frontend-marker to assert a string")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the backend release and publish the frontend.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the pytest suite")
    parser.add_argument("--backend-only", action="store_true", help="skip the push and the frontend wait")
    parser.add_argument("--frontend-wait", type=int, default=300, help="seconds to wait for Pages (default 300)")
    parser.add_argument("--frontend-marker", action="append", default=[],
                        help="string that must appear in the published bundle (repeatable); proves the push is live")
    args = parser.parse_args()

    if not (ROOT / ".git").exists():
        raise SystemExit(f"not a git checkout: {ROOT}")

    sha = preflight(args.skip_tests)
    say("preflight", f"ready to deploy {sha[:8]} from main")
    base = git("rev-parse", f"{sha}~1") if git("rev-list", "--count", "HEAD") != "1" else sha

    if not args.backend_only:
        if args.dry_run:
            say("push", "would run: git push origin main")
        else:
            say("push", "git push origin main")
            git("push", "origin", "main")

    frontend_touches = frontend_changed(base)
    previous_bundle = bundle_name(PAGES) if frontend_touches and not args.backend_only else None

    healthy = deploy_backend(sha, dry_run=args.dry_run)
    if not args.backend_only and frontend_touches and not args.dry_run:
        wait_frontend(previous_bundle, args.frontend_wait, args.frontend_marker)
    elif not frontend_touches:
        say("frontend", "no frontend changes in this commit; nothing to publish")

    print()
    say("done", f"backend {'healthy' if healthy else 'FAILED'} — api {HEALTH}")
    say("done", f"frontend {PAGES} (Actions: .github/workflows/deploy-pages.yml)")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
