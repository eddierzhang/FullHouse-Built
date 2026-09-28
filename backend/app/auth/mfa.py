"""Two-step sign-in: authenticator-app codes (TOTP, RFC 6238) and backup codes.

The shared secret is what an authenticator app is given once, by QR code,
and uses to make a new six-digit code every 30 seconds. It's encrypted at
rest with SECRET_KEY, so a copy of the database alone can't mint codes.
Backup codes are stored hashed, like passwords, and each works once.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger("fullhouse.security")

PERIOD_SECONDS = 30
DIGITS = 6
#: Codes from one step either side are accepted, for clocks that drift.
DRIFT_STEPS = 1
BACKUP_CODE_COUNT = 10

#: Used only when SECRET_KEY is unset -- local development. Production sets
#: its own (render.customer.yaml generates one); see app.config.
_DEV_KEY = "fullhouse-development-key-do-not-use-in-production"


def _fernet() -> Fernet:
    key = settings.secret_key or _DEV_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(stored: str) -> str | None:
    """The secret, or None if it can't be read (SECRET_KEY changed)."""
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except InvalidToken:
        logger.error("a two-step secret can't be decrypted: was SECRET_KEY changed?")
        return None


def new_secret() -> str:
    """160 random bits, base32 -- what authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, counter: int) -> str:
    """RFC 4226 HOTP: the code for one 30-second step."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // PERIOD_SECONDS)


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The step `code` belongs to, if it's valid now; None otherwise."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "FullHouse") -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
        f"?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={PERIOD_SECONDS}"
    )


def qr_svg(uri: str) -> str:
    """The QR code an authenticator app scans, as an SVG data URI."""
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


# --- backup codes ---------------------------------------------------------------------


def _normalize(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def _hash_backup(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode()).hexdigest()


def new_backup_codes() -> tuple[list[str], list[str]]:
    """(codes to show once, hashes to store). Codes look like "k3f9-x2mq"."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # nothing that reads as another character
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes, [_hash_backup(c) for c in codes]


def use_backup_code(stored: list[str], code: str) -> list[str] | None:
    """The remaining hashes if `code` is one of them (it's spent); None if not."""
    wanted = _hash_backup(code)
    for index, candidate in enumerate(stored):
        if hmac.compare_digest(candidate, wanted):
            return stored[:index] + stored[index + 1:]
    return None
