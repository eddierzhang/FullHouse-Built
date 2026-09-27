"""Attributes changes made during an HTTP request to the caller.

Written as raw ASGI middleware rather than a `BaseHTTPMiddleware`
subclass on purpose: BaseHTTPMiddleware runs the downstream app in a
separate task, which makes ContextVar propagation into the route handler
depend on Starlette internals. A plain ASGI callable sets the var in the
same task that awaits the app, so it always reaches the handler.
"""

from app.audit.context import Actor, HUMAN, reset_actor, set_actor

#: Stand-in for real authentication. Swap this for the authenticated
#: principal as soon as the app grows a login.
ACTOR_HEADER = b"x-actor-id"
ANONYMOUS = "anonymous"


def _actor_id(scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == ACTOR_HEADER:
            decoded = value.decode("latin-1").strip()
            if decoded:
                return decoded[:64]
    return ANONYMOUS


class AuditActorMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = set_actor(Actor(type=HUMAN, id=_actor_id(scope)))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_actor(token)
