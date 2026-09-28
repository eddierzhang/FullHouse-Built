"""Two ways a save can go wrong when several people work at once.

Retries. A double tap, or a resend after the connection dropped, sends the
same write twice. The client names each write with an `Idempotency-Key`
header; routes that must not happen twice (a delivery, a sale) store it
and answer a repeat with what the first one did.

Stale edits. Two managers open the same item; one saves, then the other's
save quietly overwrites it. An edit can send `expected`: the values its
form started from. If any of them has changed since, the save is refused
and the person sees what it is now -- the same rule undo already follows.
"""

import math
from typing import Any

from fastapi import HTTPException, Request

IDEMPOTENCY_HEADER = "idempotency-key"
MAX_KEY_LENGTH = 64


def idempotency_key(request: Request) -> str | None:
    """The request's Idempotency-Key, if it sent a usable one."""
    key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
    if not key:
        return None
    if len(key) > MAX_KEY_LENGTH or not key.isprintable():
        raise HTTPException(422, f"Idempotency-Key must be printable and at most {MAX_KEY_LENGTH} characters.")
    return key


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    # Blank and missing mean the same thing in a form.
    return (a if a != "" else None) == (b if b != "" else None)


def check_expected(record: Any, expected: dict[str, Any] | None, labels: dict[str, str] | None = None) -> None:
    """Refuse the save if any field has moved on from what the editor last saw.

    Only the fields sent are checked, so an edit to the price doesn't
    clash with someone else's change to the description.
    """
    if not expected:
        return
    moved = []
    for field, seen in expected.items():
        if not hasattr(record, field):
            continue
        now = getattr(record, field)
        if not _same(now, seen):
            label = (labels or {}).get(field, field.replace("_", " "))
            moved.append(f"{label} is now {now if now not in (None, '') else 'blank'}")
    if moved:
        raise HTTPException(
            409,
            "Someone changed this while you were editing: " + "; ".join(moved)
            + ". Check the new values and save again if you still want your change.",
        )
