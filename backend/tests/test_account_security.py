"""Accounts: invites, turning access off, reset links, signing out everywhere,
per-account lockout, and the security log that records all of it."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth.models import ADMIN, MEMBER, Invite, PasswordReset, SecurityEvent, User
from app.auth.security import hash_password
from conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD
from test_restaurant_details import _other_client, _second_restaurant
from test_restaurant_setup import _set_up

AUTH = "/api/v1/auth"


def _browser() -> TestClient:
    from app.main import app

    return TestClient(app)


def _types(db, email=None) -> list[str]:
    query = db.query(SecurityEvent).order_by(SecurityEvent.id)
    if email:
        query = query.filter(SecurityEvent.email == email)
    return [e.type for e in query]


def _teammate(db, restaurant_id, email="mo@example.com", role=MEMBER, password="a-long-password") -> User:
    user = User(email=email, name=email.split("@")[0].title(), password_hash=hash_password(password),
                role=role, restaurant_id=restaurant_id)
    db.add(user)
    db.commit()
    return user


# --- invites -------------------------------------------------------------------------


def test_an_invite_creates_one_account_in_the_restaurant_with_its_role(client, db):
    restaurant = _set_up(client, db)
    created = client.post(f"{AUTH}/invites", json={"role": "member"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert db.query(Invite).one().token_hash != token  # only a hash is kept

    newcomer = _browser()
    preview = newcomer.get(f"{AUTH}/invite/{token}").json()
    assert preview == {"restaurant_name": restaurant["name"], "role": "member", "email": None}
    joined = newcomer.post(f"{AUTH}/invite/{token}", json={"name": "Mo", "email": "mo@example.com",
                                                          "password": "a-long-password"})
    assert joined.status_code == 201, joined.text
    assert (joined.json()["role"], joined.json()["restaurant_id"]) == ("member", restaurant["id"])
    assert newcomer.get(f"{AUTH}/me").json()["email"] == "mo@example.com"  # signed straight in

    # Once only.
    again = _browser().post(f"{AUTH}/invite/{token}", json={"name": "X", "email": "x@example.com",
                                                          "password": "a-long-password"})
    assert again.status_code == 404
    assert client.get(f"{AUTH}/invites").json() == []
    assert "invite_accepted" in _types(db, "mo@example.com")


def test_an_invite_for_an_email_takes_only_that_email(client, db):
    _set_up(client, db)
    token = client.post(f"{AUTH}/invites", json={"role": "member", "email": "Mo@Example.com"}).json()["token"]

    wrong = _browser().post(f"{AUTH}/invite/{token}", json={"name": "X", "email": "x@example.com",
                                                          "password": "a-long-password"})
    assert wrong.status_code == 403
    right = _browser().post(f"{AUTH}/invite/{token}", json={"name": "Mo", "email": "mo@example.com",
                                                          "password": "a-long-password"})
    assert right.status_code == 201


def test_expired_and_cancelled_invites_are_dead(client, db):
    _set_up(client, db)
    expired = client.post(f"{AUTH}/invites", json={}).json()
    cancelled = client.post(f"{AUTH}/invites", json={}).json()
    db.query(Invite).filter(Invite.id == expired["id"]).update(
        {Invite.expires_at: datetime.now(timezone.utc) - timedelta(minutes=1)})
    db.commit()
    assert client.delete(f"{AUTH}/invites/{cancelled['id']}").status_code == 204

    for token in (expired["token"], cancelled["token"]):
        assert _browser().get(f"{AUTH}/invite/{token}").status_code == 404
    assert client.get(f"{AUTH}/invites").json() == []


def test_who_may_invite_whom(client, db):
    restaurant = _set_up(client, db)
    _teammate(db, restaurant["id"], "admin@example.com", ADMIN)
    _teammate(db, restaurant["id"], "mo@example.com", MEMBER)
    admin, member = _other_client("admin@example.com"), _other_client("mo@example.com")

    assert client.post(f"{AUTH}/invites", json={"role": "admin"}).status_code == 201
    assert admin.post(f"{AUTH}/invites", json={"role": "member"}).status_code == 201
    assert admin.post(f"{AUTH}/invites", json={"role": "admin"}).status_code == 403
    assert member.post(f"{AUTH}/invites", json={"role": "member"}).status_code == 403
    assert "access_denied" in _types(db, "mo@example.com")


def test_signup_is_closed_once_the_restaurant_exists(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.allow_signup", False)
    _set_up(client, db)
    walk_in = _browser().post(f"{AUTH}/signup", json={"name": "W", "email": "w@example.com",
                                                     "password": "a-long-password"})
    assert walk_in.status_code == 403


# --- turning access off --------------------------------------------------------------


def test_turning_access_off_ends_every_session_at_once(client, db):
    restaurant = _set_up(client, db)
    mo = _teammate(db, restaurant["id"])
    phone, laptop = _other_client("mo@example.com"), _other_client("mo@example.com")
    assert phone.get(f"{AUTH}/me").status_code == 200

    off = client.post(f"{AUTH}/team/{mo.id}/disable")
    assert off.status_code == 200 and off.json()["disabled_at"]
    assert phone.get(f"{AUTH}/me").status_code == 401
    assert laptop.get("/api/v1/restaurant/staff").status_code == 401

    # The right password is still refused, with a reason; a wrong one is just wrong.
    refused = _browser().post(f"{AUTH}/login", json={"email": "mo@example.com", "password": "a-long-password"})
    assert refused.status_code == 403 and "turned off" in refused.json()["detail"]
    assert _browser().post(f"{AUTH}/login", json={"email": "mo@example.com", "password": "nope-nope"}).status_code == 401

    assert client.post(f"{AUTH}/team/{mo.id}/enable").status_code == 200
    assert _browser().post(f"{AUTH}/login", json={"email": "mo@example.com",
                                                 "password": "a-long-password"}).status_code == 200
    assert {"access_disabled", "login_disabled", "access_enabled"} <= set(_types(db, "mo@example.com"))


def test_who_may_turn_whose_access_off(client, db):
    restaurant = _set_up(client, db)
    owner = db.query(User).filter(User.email == TEST_USER_EMAIL).one()
    admin_user = _teammate(db, restaurant["id"], "admin@example.com", ADMIN)
    other_admin = _teammate(db, restaurant["id"], "admin2@example.com", ADMIN)
    member = _teammate(db, restaurant["id"], "mo@example.com", MEMBER)
    admin = _other_client("admin@example.com")

    assert admin.post(f"{AUTH}/team/{member.id}/disable").status_code == 200
    assert admin.post(f"{AUTH}/team/{other_admin.id}/disable").status_code == 403
    assert admin.post(f"{AUTH}/team/{owner.id}/disable").status_code == 403
    assert admin.post(f"{AUTH}/team/{admin_user.id}/disable").status_code == 403  # not yourself
    assert client.post(f"{AUTH}/team/{owner.id}/disable").status_code == 403
    assert client.post(f"{AUTH}/team/{other_admin.id}/disable").status_code == 200

    # Another restaurant's owner can't reach these accounts at all.
    _, stranger = _second_restaurant(db)
    assert stranger.post(f"{AUTH}/team/{member.id}/enable").status_code == 404


# --- reset links ---------------------------------------------------------------------


def test_a_reset_link_sets_a_new_password_once_and_ends_old_sessions(client, db):
    restaurant = _set_up(client, db)
    mo = _teammate(db, restaurant["id"])
    old_session = _other_client("mo@example.com")

    link = client.post(f"{AUTH}/team/{mo.id}/reset-link")
    assert link.status_code == 200, link.text
    token = link.json()["token"]

    browser = _browser()
    assert browser.get(f"{AUTH}/reset/{token}").json() == {"name": mo.name, "email": "mo@example.com"}
    assert browser.post(f"{AUTH}/reset/{token}", json={"password": "short"}).status_code == 422
    assert browser.post(f"{AUTH}/reset/{token}", json={"password": "a-brand-new-one"}).status_code == 200

    assert browser.get(f"{AUTH}/me").status_code == 200  # signed in with it
    assert old_session.get(f"{AUTH}/me").status_code == 401
    assert _browser().post(f"{AUTH}/reset/{token}", json={"password": "again-again"}).status_code == 404
    assert _browser().post(f"{AUTH}/login", json={"email": "mo@example.com",
                                                 "password": "a-brand-new-one"}).status_code == 200
    assert {"reset_link_issued", "password_reset"} <= set(_types(db, "mo@example.com"))


def test_reset_links_expire_and_a_new_one_voids_the_old(client, db):
    restaurant = _set_up(client, db)
    mo = _teammate(db, restaurant["id"])
    first = client.post(f"{AUTH}/team/{mo.id}/reset-link").json()["token"]
    second = client.post(f"{AUTH}/team/{mo.id}/reset-link").json()["token"]
    assert _browser().get(f"{AUTH}/reset/{first}").status_code == 404

    db.query(PasswordReset).update({PasswordReset.expires_at: datetime.now(timezone.utc) - timedelta(seconds=1)})
    db.commit()
    assert _browser().get(f"{AUTH}/reset/{second}").status_code == 404


def test_members_cannot_issue_reset_links(client, db):
    restaurant = _set_up(client, db)
    other = _teammate(db, restaurant["id"], "ana@example.com")
    _teammate(db, restaurant["id"])
    member = _other_client("mo@example.com")
    assert member.post(f"{AUTH}/team/{other.id}/reset-link").status_code == 403


def test_the_command_line_link_rescues_a_locked_out_owner(client, db, monkeypatch):
    import scripts.reset_link as script
    from app.db import session as app_session

    _set_up(client, db)
    monkeypatch.setattr(script, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(app_session, "SessionLocal", lambda: db)
    assert script.main(TEST_USER_EMAIL, "https://example.test") == 0
    reset = db.query(PasswordReset).one()
    assert reset.created_by is None


# --- sessions and sign-in --------------------------------------------------------------


def test_signing_out_everywhere_ends_every_session(client, db):
    _set_up(client, db)
    other_browser = _other_client(TEST_USER_EMAIL, TEST_USER_PASSWORD)
    assert client.post(f"{AUTH}/logout-everywhere").status_code == 204
    assert client.get(f"{AUTH}/me").status_code == 401
    assert other_browser.get(f"{AUTH}/me").status_code == 401


def test_many_addresses_guessing_one_account_are_stopped(client, db):
    _set_up(client, db)
    for n in range(10):
        _browser().post(f"{AUTH}/login", json={"email": TEST_USER_EMAIL, "password": "wrong-guess"},
                        headers={"x-forwarded-for": f"10.0.0.{n}"})
    blocked = _browser().post(f"{AUTH}/login", json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD},
                              headers={"x-forwarded-for": "10.0.0.99"})
    assert blocked.status_code == 429
    assert _types(db, TEST_USER_EMAIL).count("login_failed") == 10
    assert "login_blocked" in _types(db, TEST_USER_EMAIL)


def test_a_sign_in_from_a_new_device_is_flagged(client, db):
    _set_up(client, db)
    login = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}
    _browser().post(f"{AUTH}/login", json=login, headers={"user-agent": "Laptop"})
    _browser().post(f"{AUTH}/login", json=login, headers={"user-agent": "Laptop"})
    assert "new_device" not in _types(db, TEST_USER_EMAIL)
    _browser().post(f"{AUTH}/login", json=login, headers={"user-agent": "Unknown phone"})
    assert _types(db, TEST_USER_EMAIL)[-2:] == ["new_device", "login_succeeded"]


# --- the security log ---------------------------------------------------------------


def test_the_security_log_is_for_this_restaurants_managers(client, db):
    restaurant = _set_up(client, db)
    _teammate(db, restaurant["id"])
    member = _other_client("mo@example.com")
    member.post("/api/v1/agents/runs", json={"agent_key": "maestro", "input": "x"})  # refused, and logged

    log = client.get(f"{AUTH}/security-events").json()
    denied = next(e for e in log if e["type"] == "access_denied")
    assert denied["email"] == "mo@example.com" and denied["detail"]["path"] == "/api/v1/agents/runs"
    assert denied["label"] == "Tried something their role doesn't allow"

    assert member.get(f"{AUTH}/security-events").status_code == 403
    _, stranger = _second_restaurant(db)
    assert all(e["email"] != "mo@example.com" for e in stranger.get(f"{AUTH}/security-events").json())
