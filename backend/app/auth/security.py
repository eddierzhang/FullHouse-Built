"""Password hashing and session tokens, on the standard library alone.

scrypt rather than bcrypt or argon2: it is memory-hard, it ships with
Python's hashlib, and it needs no compiled dependency in the image.
"""

import base64
import hashlib
import hmac
import secrets

# ~16 MB and a few tens of milliseconds per hash: slow enough to make
# guessing expensive, fast enough that signing in doesn't feel it.
_N, _R, _P = 2**14, 8, 1
_KEY_LENGTH = 32


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_KEY_LENGTH, maxmem=64 * 1024**2)


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash`, so the cost can be raised without breaking old hashes."""
    salt = secrets.token_bytes(16)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(_scrypt(password, salt, _N, _R, _P))}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    except ValueError:
        return False
    return hmac.compare_digest(actual, base64.b64decode(expected))


#: Checked against when the email is unknown, so a miss takes as long as a
#: wrong password and response times don't reveal who has an account.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
