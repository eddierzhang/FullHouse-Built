"""Process-wide setup, in one place.

Every entry point that touches the database must call `setup()` before
opening a session -- the API, seed and management scripts, and any
future worker. The audit listener is registered here rather than in
`main.py` because a script that never imports the FastAPI app would
otherwise write to the database with no change recorded, silently.
"""

from app.audit import listener as audit_listener


def setup() -> None:
    """Idempotent; safe to call from every entry point."""
    audit_listener.install()
