"""Who may change what: every route that writes is for owners and admins,
unless it is on the short list staff are meant to use.

The first test walks the app's routes rather than listing them, so a new
route that writes without a role check fails here until it gets one or is
added, with a reason, to STAFF_MAY.
"""

import pytest
from fastapi.routing import APIRoute

from app.main import app
from test_restaurant_details import _member
from test_restaurant_setup import _set_up

#: Dependencies that let only owners and admins through.
MANAGER_GUARDS = {"manager", "hr_manager", "editor", "_manager"}

#: Writes staff may make, and where each is limited instead.
STAFF_MAY = {
    ("POST", "/api/v1/auth/signup"): "public; signup can be turned off",
    ("POST", "/api/v1/auth/login"): "public",
    ("POST", "/api/v1/auth/logout"): "anyone may end their own session",
    ("POST", "/api/v1/auth/logout-everywhere"): "anyone may end all of their own sessions",
    ("POST", "/api/v1/auth/invite/{token}"): "public; needs a live invite link",
    ("POST", "/api/v1/auth/reset/{token}"): "public; needs a live reset link",
    ("POST", "/api/v1/auth/login/mfa"): "public; needs a pending sign-in whose password was right",
    ("POST", "/api/v1/auth/mfa/setup"): "your own two-step sign-in",
    ("POST", "/api/v1/auth/mfa/enable"): "your own two-step sign-in",
    ("POST", "/api/v1/auth/mfa/backup-codes"): "your own two-step sign-in, with a current code",
    ("POST", "/api/v1/auth/mfa/disable"): "your own, with a current code; refused where it's required",
    ("PATCH", "/api/v1/auth/team/{user_id}"): "owners only, checked in the route",
    ("POST", "/api/v1/restaurant/inventory-items"): "staff add stock items",
    ("POST", "/api/v1/support/feedback"): "anyone signed in may send feedback; rate-limited per person",
    ("POST", "/api/v1/support/client-errors"): "a crash report from the person's own browser; rate-limited per person",
    ("POST", "/api/v1/restaurant/inventory-items/{inventory_item_id}/adjustments"): "staff record deliveries and use",
    ("PATCH", "/api/v1/restaurant/inventory-items/{inventory_item_id}"): "item details are checked in the route",
    ("POST", "/api/v1/restaurant/orders"): "staff take orders",
    ("PATCH", "/api/v1/restaurant/staff/{staff_id}"): "own phone and emergency contact only, checked in the route",
}


def _routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):  # an included router
            yield from _routes(route.original_router.routes)


def _dependency_names(dependant, names=None) -> set[str]:
    names = set() if names is None else names
    for sub in dependant.dependencies:
        names.add(getattr(sub.call, "__name__", ""))
        _dependency_names(sub, names)
    return names


def _writes():
    for route in _routes(app.routes):
        for method in sorted(route.methods - {"GET", "HEAD", "OPTIONS"}):
            yield method, route.path, route


def test_every_write_is_for_managers_unless_staff_are_meant_to_use_it():
    unguarded = [
        f"{method} {path}"
        for method, path, route in _writes()
        if (method, path) not in STAFF_MAY and not _dependency_names(route.dependant) & MANAGER_GUARDS
    ]
    assert unguarded == []
    # And the allowlist names only routes that exist.
    assert set(STAFF_MAY) <= {(method, path) for method, path, _ in _writes()}


MANAGER_ONLY = [
    ("POST", "/api/v1/restaurant/menu-items"),
    ("PATCH", "/api/v1/restaurant/menu-items/x"),
    ("DELETE", "/api/v1/restaurant/menu-items/x"),
    ("PUT", "/api/v1/restaurant/menu-items/x/recipe"),
    ("POST", "/api/v1/restaurant/suppliers"),
    ("PATCH", "/api/v1/restaurant/suppliers/x"),
    ("DELETE", "/api/v1/restaurant/suppliers/x"),
    ("GET", "/api/v1/restaurant/profit-summary"),
    ("GET", "/api/v1/restaurant/forecast"),
    ("PATCH", "/api/v1/agents/definitions/maestro"),
    ("POST", "/api/v1/agents/runs"),
    ("POST", "/api/v1/agents/runs/x/cancel"),
    ("POST", "/api/v1/agents/actions/x/approve"),
    ("POST", "/api/v1/agents/actions/x/reject"),
    ("POST", "/api/v1/changes/1/revert"),
]


@pytest.mark.parametrize("method,path", MANAGER_ONLY)
def test_staff_are_refused_manager_actions(client, db, method, path):
    _set_up(client, db)
    member = _member(client, db)

    response = member.request(method, path, json={})
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Only the owner or an admin can do that."


def test_staff_can_still_read_what_they_work_with(client, db):
    _set_up(client, db)
    member = _member(client, db)

    for path in ("/menu-items", "/menu-performance", "/suppliers", "/orders", "/supply-chain",
                 "/inventory-items", "/staff", "/shifts"):
        assert member.get(f"/api/v1/restaurant{path}").status_code == 200, path
    for path in ("/agents/definitions", "/agents/runs", "/agents/actions", "/changes"):
        assert member.get(f"/api/v1{path}").status_code == 200, path


def test_every_response_carries_the_security_headers(anon_client):
    plain = anon_client.get("/health")
    assert plain.headers["x-frame-options"] == "DENY"
    assert plain.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in plain.headers["content-security-policy"]
    assert "strict-transport-security" not in plain.headers  # only once reached over HTTPS
    behind_render = anon_client.get("/health", headers={"x-forwarded-proto": "https"})
    assert behind_render.headers["strict-transport-security"].startswith("max-age=")
