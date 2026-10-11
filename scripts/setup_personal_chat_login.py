#!/usr/bin/env python3
"""Create the private personal chat login config without accepting secrets on argv."""
import base64, getpass, hashlib, json, os, secrets, sys, tempfile
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "data" / "personal-chat-login.json"

def main():
    target = Path(os.environ.get("PERSONAL_CHAT_LOGIN_PATH", DEFAULT))
    if target.exists():
        raise SystemExit(f"Refusing to replace existing personal chat config: {target}. Use a separate, reviewed rotation procedure.")
    code = sys.stdin.readline().rstrip("\r\n") if not sys.stdin.isatty() else getpass.getpass("Personal chat login code: ")
    if not code or len(code) > 256:
        raise SystemExit("Login code must contain 1 to 256 characters.")
    salt = secrets.token_bytes(32)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, iterations, dklen=32)
    payload = {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(), "iterations": iterations, "chat_credential": secrets.token_urlsafe(48)}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".personal-chat-login-", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.link(tmp, target)
        os.chmod(target, 0o600)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
    print(f"Personal chat login config written with mode 0600: {target}")

if __name__ == "__main__": main()
