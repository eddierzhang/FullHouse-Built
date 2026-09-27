"""Starts every HTTP request as an anonymous human actor.

The signed-in user replaces this as soon as `app.auth.deps.current_user`
resolves, so changes on protected routes carry their email. Anything that
changes data without a user -- signing up, signing in -- stays anonymous.

Written as raw ASGI middleware rather than a `BaseHTTPMiddleware`
subclass on purpose: BaseHTTPMiddleware runs the downstream app in a
separate task, which makes ContextVar propagation into the route handler
depend on Starlette internals. A plain ASGI callable sets the var in the
same task that awaits the app, so it always reaches the handler.
"""

from app.audit.context import Actor, HUMAN, reset_actor, set_actor

ANONYMOUS = "anonymous"


class AuditActorMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = set_actor(Actor(type=HUMAN, id=ANONYMOUS))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_actor(token)
