"""Change tracking: who changed what, when, and from what to what.

`listener.install()` wires a session-level hook that records every ORM
insert/update/delete into `change_log`. Attribution comes from the
`Actor` in `context`, set by the HTTP middleware for operator requests
and by the agent runner for agent-driven work.
"""

from app.audit.context import Actor, actor_context, get_actor
from app.audit.models import ChangeLog

__all__ = ["Actor", "ChangeLog", "actor_context", "get_actor"]
