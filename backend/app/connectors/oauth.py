"""OAuth for Square, Gusto and QuickBooks: a signed state, a token exchange, a connection row.

Client ids and secrets come from the environment (SQUARE_*, GUSTO_*, QBO_*). Without them the
provider is listed as not available on this site. Tokens are stored encrypted and refreshed
on use inside the worker.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import httpx

from app.config import settings

SPECS = {
    "square": {
        "authorize": {"production": "https://connect.squareup.com/oauth2/authorize", "sandbox": "https://connect.squareupsandbox.com/oauth2/authorize"},
        "token": {"production": "https://connect.squareup.com/oauth2/token", "sandbox": "https://connect.squareupsandbox.com/oauth2/token"},
        "scopes": "ORDERS_READ ITEMS_READ MERCHANT_PROFILE_READ",
        "id_env": "SQUARE_APP_ID", "secret_env": "SQUARE_APP_SECRET", "env_env": "SQUARE_ENV",
    },
    "gusto": {
        "authorize": {"production": "https://api.gusto.com/oauth/authorize", "sandbox": "https://api.gusto-demo.com/oauth/authorize"},
        "token": {"production": "https://api.gusto.com/oauth/token", "sandbox": "https://api.gusto-demo.com/oauth/token"},
        "scopes": "", "id_env": "GUSTO_CLIENT_ID", "secret_env": "GUSTO_CLIENT_SECRET", "env_env": "GUSTO_ENV",
    },
    "qbo": {
        "authorize": {"production": "https://appcenter.intuit.com/connect/oauth2", "sandbox": "https://appcenter.intuit.com/connect/oauth2"},
        "token": {"production": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer", "sandbox": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"},
        "scopes": "com.intuit.quickbooks.accounting", "id_env": "QBO_CLIENT_ID", "secret_env": "QBO_CLIENT_SECRET", "env_env": "QBO_ENV",
    },
}


def environment(provider: str) -> str:
    return (os.getenv(SPECS[provider]["env_env"], "sandbox") or "sandbox").strip().lower()


def configured(provider: str) -> bool:
    spec = SPECS.get(provider)
    return bool(spec and os.getenv(spec["id_env"], "").strip() and os.getenv(spec["secret_env"], "").strip())


def redirect_uri(provider: str) -> str:
    return f"{settings.public_url}/api/v1/connect/oauth/{provider}/callback"


def _sign(message: str) -> str:
    key = (settings.secret_key or "development-only-key").encode()
    return base64.urlsafe_b64encode(hmac.new(key, message.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def make_state(provider: str, restaurant_id: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"p": provider, "r": restaurant_id, "t": int(time.time())}).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def read_state(state: str, max_age: int = 900) -> tuple[str, str] | None:
    try:
        body, sig = state.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(body), sig):
        return None
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if time.time() - data.get("t", 0) > max_age:
        return None
    return data["p"], data["r"]


def authorize_url(provider: str, restaurant_id: str) -> str:
    spec = SPECS[provider]
    params = {"client_id": os.getenv(spec["id_env"], ""), "response_type": "code", "redirect_uri": redirect_uri(provider),
              "state": make_state(provider, restaurant_id)}
    if spec["scopes"]:
        params["scope"] = spec["scopes"]
    if provider == "square":
        params["session"] = "false"
    return f"{spec['authorize'][environment(provider)]}?{urlencode(params)}"


def exchange_code(provider: str, code: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Trade the code for tokens. Returns what the provider sent (access_token, refresh_token, expires, realm...)."""
    spec = SPECS[provider]
    client_id, secret = os.getenv(spec["id_env"], ""), os.getenv(spec["secret_env"], "")
    url = spec["token"][environment(provider)]
    with httpx.Client(timeout=30, transport=transport) as http:
        if provider == "square":
            response = http.post(url, json={"client_id": client_id, "client_secret": secret, "code": code,
                                            "grant_type": "authorization_code", "redirect_uri": redirect_uri(provider)})
        elif provider == "gusto":
            response = http.post(url, data={"client_id": client_id, "client_secret": secret, "code": code,
                                            "grant_type": "authorization_code", "redirect_uri": redirect_uri(provider)})
        else:
            response = http.post(url, data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider)},
                                 auth=(client_id, secret), headers={"Accept": "application/json"})
    if response.status_code >= 400:
        raise RuntimeError(f"{provider} refused the sign-in ({response.status_code}).")
    return response.json()


def refresh(provider: str, credentials: dict, transport: httpx.BaseTransport | None = None) -> dict:
    """A fresh access token from the refresh token; the refresh token rotates where the provider rotates it."""
    spec = SPECS[provider]
    client_id, secret = os.getenv(spec["id_env"], ""), os.getenv(spec["secret_env"], "")
    url = spec["token"][environment(provider)]
    token = credentials.get("refresh_token")
    if not token:
        return credentials
    with httpx.Client(timeout=30, transport=transport) as http:
        if provider == "square":
            response = http.post(url, json={"client_id": client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": token})
        elif provider == "gusto":
            response = http.post(url, data={"client_id": client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": token})
        else:
            response = http.post(url, data={"grant_type": "refresh_token", "refresh_token": token}, auth=(client_id, secret),
                                 headers={"Accept": "application/json"})
    if response.status_code >= 400:
        raise RuntimeError(f"{provider} refused the token refresh ({response.status_code}); reconnect it.")
    fresh = response.json()
    return {**credentials, **{k: v for k, v in fresh.items() if k in ("access_token", "refresh_token", "expires_in", "expires_at")}}
