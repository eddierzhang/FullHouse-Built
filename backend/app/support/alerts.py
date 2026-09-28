"""Error alerts, and logs worth reading.

With SENTRY_DSN set, every unhandled error -- an API request, an agent run,
a crash reported by someone's browser -- goes to Sentry, which emails the
people running the site about each new kind. Without it nothing leaves the
server; errors are still logged, one line each, to wherever the host
collects stdout.

Nothing personal goes to Sentry: no cookies, no request bodies, no
passwords or tokens, and people are identified only by an opaque id.
"""

import logging
import os
import re

from app.config import settings

logger = logging.getLogger(__name__)

#: Header and field names whose values are never sent anywhere.
_SECRET = ("cookie", "authorization", "password", "token", "secret", "code", "idempotency-key")

#: One-time links carry a live token: /auth/invite/<token> on the API, #invite/<token> in the app.
_TOKEN_PATH = re.compile(r"((?:^|[/#])(?:invite|reset)/)[^/?#\s)]+")
#: A query string, wherever it appears in the text -- API keys travel there.
_QUERY = re.compile(r"\?[^\s)#]*")

_configured = False


def _clean_url(text: str) -> str:
    """No query strings and no one-time tokens -- in a URL, a route, or a whole stack trace."""
    return _TOKEN_PATH.sub(r"\1[token]", _QUERY.sub("", text))


def configure_logging() -> None:
    """One readable line per record, INFO and up, unless the host set logging up already."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO").upper(),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    # The security log is always kept, whatever LOG_LEVEL says.
    logging.getLogger("fullhouse.security").setLevel(logging.INFO)


def _scrub(event: dict, hint: dict) -> dict:
    request = event.get("request") or {}
    request.pop("cookies", None)
    request.pop("data", None)  # bodies can hold passwords, two-step codes and people's details
    request.pop("query_string", None)
    if request.get("url"):
        request["url"] = _clean_url(request["url"])
    if event.get("transaction"):
        event["transaction"] = _clean_url(event["transaction"])
    # Breadcrumbs of outgoing calls: a provider's API key can sit in the URL.
    for crumb in (event.get("breadcrumbs") or {}).get("values", []):
        data = crumb.get("data") or {}
        if isinstance(data.get("url"), str):
            data["url"] = _clean_url(data["url"])
        data.pop("http.query", None)
    headers = request.get("headers") or {}
    for name in list(headers):
        if any(secret in name.lower() for secret in _SECRET):
            headers[name] = "[removed]"
    user = event.get("user") or {}
    event["user"] = {"id": user["id"]} if user.get("id") else {}
    return event


def configure_sentry() -> bool:
    """Start sending errors to Sentry if a DSN is configured. Returns whether it did."""
    global _configured
    if _configured or not settings.sentry_dsn:
        return _configured
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        send_default_pii=False,
        before_send=_scrub,
        # Name errors by route template ("/auth/invite/{token}"), never the raw URL.
        integrations=[StarletteIntegration(transaction_style="url"), FastApiIntegration(transaction_style="url")],
        # Errors only: no performance tracing, so no request paths or timings
        # are collected beyond the errors themselves.
        traces_sample_rate=0.0,
    )
    _configured = True
    logger.info("error alerts: sending to Sentry (%s)", settings.sentry_environment)
    return True


def report(message: str, **context) -> None:
    """Log an error the app caught itself -- and, with Sentry on, alert on it."""
    logger.error("%s %s", message, context or "")
    if _configured:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for key, value in context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_message(message, level="error")
