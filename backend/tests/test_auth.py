from datetime import datetime, timedelta, timezone

import pytest

from app.auth.deps import SESSION_COOKIE
from app.auth.models import AuthSession, User
from app.auth.routes import _FailedLogins
from app.auth.security import hash_password, verify_password
from conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD

PROTECTED = [
    ("get", "/api/v1/restaurant/menu-items"),
    ("post", "/api/v1/restaurant/menu-items"),
    ("get", "/api/v1/agents/runtime"),
    ("get", "/api/v1/agents/actions"),
    ("post", "/api/v1/agents/runs"),
    ("get", "/api/v1/changes"),
    ("get", "/api/v1/auth/me"),
]


def _signup(client, email="ana@example.com", password="a-long-password", name="Ana"):
    return client.post("/api/v1/auth/signup", json={"name": name, "email": email, "password": password})


@pytest.mark.parametrize("method,path", PROTECTED)
def test_data_routes_refuse_anyone_signed_out(anon_client, method, path):
    response = getattr(anon_client, method)(path)
    assert response.status_code == 401


def test_health_and_login_options_stay_public(anon_client):
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/api/v1/auth/options").json() == {"signup_enabled": True, "demo": None}


def test_signup_signs_you_in(anon_client):
    response = _signup(anon_client, email="  Ana@Example.COM ")
    assert response.status_code == 201
    assert response.json()["email"] == "ana@example.com"
    assert "password_hash" not in response.json()

    cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()

    assert anon_client.get("/api/v1/auth/me").json()["name"] == "Ana"
    assert anon_client.get("/api/v1/restaurant/menu-items").status_code == 200


def test_passwords_are_hashed_not_stored(anon_client, db):
    _signup(anon_client)
    user = db.query(User).one()
    assert "a-long-password" not in user.password_hash
    assert verify_password("a-long-password", user.password_hash)
    assert not verify_password("a-long-passwore", user.password_hash)


def test_session_tokens_are_stored_hashed(anon_client, db):
    _signup(anon_client)
    token = anon_client.cookies.get(SESSION_COOKIE)
    assert db.get(AuthSession, token) is None
    assert db.query(AuthSession).count() == 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Ana", "email": "not-an-email", "password": "a-long-password"},
        {"name": "Ana", "email": "ana@example.com", "password": "short"},
        {"name": "   ", "email": "ana@example.com", "password": "a-long-password"},
    ],
)
def test_signup_rejects_bad_input(anon_client, body):
    assert anon_client.post("/api/v1/auth/signup", json=body).status_code == 422


def test_an_email_can_only_sign_up_once(anon_client):
    _signup(anon_client)
    response = _signup(anon_client, email="ANA@example.com")
    assert response.status_code == 409


def test_with_signup_off_the_owner_starts_and_the_team_needs_invites(anon_client, db, monkeypatch):
    from app.restaurant.models import Restaurant

    monkeypatch.setattr("app.config.settings.allow_signup", False)
    # No restaurant yet: signing up is how its owner starts.
    assert anon_client.get("/api/v1/auth/options").json()["signup_enabled"] is True
    assert _signup(anon_client).json()["role"] == "owner"

    db.add(Restaurant(name="Casa", completed_steps=[]))
    db.commit()
    anon_client.post("/api/v1/auth/logout")
    assert anon_client.get("/api/v1/auth/options").json()["signup_enabled"] is False
    refused = _signup(anon_client, email="walk-in@example.com")
    assert refused.status_code == 403 and "invite" in refused.json()["detail"]


def test_login_with_the_right_password(client):
    client.post("/api/v1/auth/logout")
    response = client.post("/api/v1/auth/login", json={"email": TEST_USER_EMAIL.upper(), "password": TEST_USER_PASSWORD})
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me").json()["email"] == TEST_USER_EMAIL


@pytest.mark.parametrize("email,password", [(TEST_USER_EMAIL, "wrong-password"), ("nobody@example.com", "whatever")])
def test_wrong_credentials_get_the_same_answer(client, email, password):
    client.post("/api/v1/auth/logout")
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "That email and password don't match."
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_ends_the_session_on_the_server(client, db):
    token = client.cookies.get(SESSION_COOKIE)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert db.query(AuthSession).count() == 0

    # Replaying the old cookie must not work either.
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_an_expired_session_is_refused_and_removed(client, db):
    session = db.query(AuthSession).one()
    session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    assert client.get("/api/v1/auth/me").status_code == 401
    assert db.query(AuthSession).count() == 0


def test_repeated_failures_lock_out_further_attempts(client):
    client.post("/api/v1/auth/logout")
    for _ in range(_FailedLogins.LIMIT):
        client.post("/api/v1/auth/login", json={"email": TEST_USER_EMAIL, "password": "guess"})

    # Even the right password is refused while locked out.
    response = client.post("/api/v1/auth/login", json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD})
    assert response.status_code == 429


def test_login_tables_stay_out_of_the_change_history(client, changes):
    assert changes(entity_type="users") == []
    assert changes(entity_type="auth_sessions") == []


def test_approving_is_attributed_to_the_signed_in_user(client, db, changes):
    from app.agents.models import AgentAction, AgentDefinition, AgentRun
    from app.restaurant.models import InventoryItem

    agent = AgentDefinition(key="inventory", name="Inventory", description="", role="subagent",
                            model="m", system_prompt="", tool_allowlist=[])
    item = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=2, reorder_threshold=5, reorder_qty=10)
    db.add_all([agent, item])
    db.flush()
    run = AgentRun(agent_definition_id=agent.id, status="succeeded", trigger_type="manual")
    db.add(run)
    db.flush()
    action = AgentAction(run_id=run.id, agent_definition_id=agent.id, action_type="inventory_reorder",
                         payload={"inventory_item_id": item.id, "quantity": 10})
    db.add(action)
    db.commit()

    assert client.post(f"/api/v1/agents/actions/{action.id}/approve", json={}).status_code == 200

    (restock,) = changes(entity_type="inventory_items", operation="update")
    assert restock.actor_id == TEST_USER_EMAIL


def test_demo_offers_a_working_account(anon_client, db, monkeypatch):
    from app.demo import DEMO_EMAIL, DEMO_PASSWORD, reset_demo_data

    monkeypatch.setattr("app.config.settings.demo_mode", True)
    reset_demo_data(db)

    demo = anon_client.get("/api/v1/auth/options").json()["demo"]
    assert demo == {"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    assert anon_client.post("/api/v1/auth/login", json=demo).status_code == 200


def test_hashes_carry_their_parameters():
    stored = hash_password("pw")
    assert stored.startswith("scrypt$16384$8$1$")
    assert hash_password("pw") != stored  # salted
    assert not verify_password("pw", "garbage")
