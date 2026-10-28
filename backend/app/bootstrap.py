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
    from app.restaurant import inventory
    from app.connectors import models as _connector_models  # noqa: F401  registered with the metadata
    from app.platform import models as _platform_models  # noqa: F401
    from app.sales import models as _sales_models  # noqa: F401
    from app.documents import models as _document_models  # noqa: F401
    from app.recipes import models as _recipe_models  # noqa: F401
    from app.forecast import models as _forecast_models  # noqa: F401
    from app.calendar import models as _calendar_models  # noqa: F401

    from app.agents import guard

    audit_listener.install()
    # The agent writes only proposals: refused at flush time inside any tool call.
    guard.install()
    # Stock history, for the same reason: no path may change stock unrecorded.
    inventory.install()
