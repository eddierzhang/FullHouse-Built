"""Credentials at rest: encrypted with a key derived from SECRET_KEY (Fernet: AES-128-CBC + HMAC).

`decrypt` is imported only by `app.connectors.registry`, which runs in the worker. A test
asserts no route module imports it, so a leaked web process cannot read credentials.
"""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _fernet() -> Fernet:
    key = settings.secret_key or "development-only-key"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("connectors:" + key).encode()).digest()))


def encrypt(credentials: dict) -> str:
    return _fernet().encrypt(json.dumps(credentials, sort_keys=True).encode()).decode()


def decrypt(stored: str | None) -> dict:
    if not stored:
        return {}
    try:
        return json.loads(_fernet().decrypt(stored.encode()))
    except (InvalidToken, ValueError):
        return {}
