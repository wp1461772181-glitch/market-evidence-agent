"""Encrypt short-lived model API keys at rest with the standard library.

The deployment has no cryptography dependency and does not need one: every
value is encrypted with a random nonce, an HMAC-SHA256 counter-mode keystream,
and an HMAC-SHA256 integrity tag derived from the deployment secret. This is a
deliberate small, auditable construction for one field in one table, not a
general-purpose replacement for a vetted AEAD library.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

_VERSION = b"me-cred-v1"
_NONCE_BYTES = 16
_TAG_BYTES = 16


class CredentialEncryptionError(RuntimeError):
    """The deployment secret is missing or a stored value cannot be decrypted."""


def _load_project_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def _encryption_key() -> bytes:
    _load_project_env()
    secret = os.getenv("MODEL_CREDENTIAL_KEY") or os.getenv("APP_API_ACCESS_KEY")
    if not secret or len(secret) < 16:
        raise CredentialEncryptionError(
            "MODEL_CREDENTIAL_KEY or APP_API_ACCESS_KEY is required to encrypt model credentials"
        )
    return hashlib.sha256(
        b"market-evidence/material-analysis-credentials/v1:" + secret.encode("utf-8")
    ).digest()


def encrypt_secret(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise CredentialEncryptionError("credential must be a non-empty string")
    key = _encryption_key()
    nonce = os.urandom(_NONCE_BYTES)
    data = value.encode("utf-8")
    ciphertext = _xor(data, _keystream(key, nonce, len(data)))
    tag = hmac.new(key, b"tag" + nonce + ciphertext, hashlib.sha256).digest()[:_TAG_BYTES]
    return base64.urlsafe_b64encode(_VERSION + nonce + tag + ciphertext).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise CredentialEncryptionError("stored credential is empty")
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CredentialEncryptionError("stored credential is invalid") from exc
    header = len(_VERSION)
    if len(raw) < header + _NONCE_BYTES + _TAG_BYTES or not raw.startswith(_VERSION):
        raise CredentialEncryptionError("stored credential is invalid")
    nonce = raw[header:header + _NONCE_BYTES]
    tag = raw[header + _NONCE_BYTES:header + _NONCE_BYTES + _TAG_BYTES]
    ciphertext = raw[header + _NONCE_BYTES + _TAG_BYTES:]
    key = _encryption_key()
    expected = hmac.new(key, b"tag" + nonce + ciphertext, hashlib.sha256).digest()[:_TAG_BYTES]
    if not hmac.compare_digest(tag, expected):
        raise CredentialEncryptionError("stored credential failed its integrity check")
    return _xor(ciphertext, _keystream(key, nonce, len(ciphertext))).decode("utf-8")


def secret_last4(token: str) -> str | None:
    try:
        value = decrypt_secret(token)
    except CredentialEncryptionError:
        return None
    return value[-4:] if len(value) >= 4 else None


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    chunks: list[bytes] = []
    produced = 0
    counter = 0
    while produced < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        chunks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(chunks)[:length]


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(left ^ right for left, right in zip(data, stream))
