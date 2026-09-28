"""Two-step sign-in: the code algorithm, setup, signing in with it, and the
requirement for owners and admins."""

import base64

import pytest

from app.auth import mfa
from app.auth.models import ADMIN, MEMBER, User
from conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD
from test_account_security import _browser, _teammate, _types
from test_restaurant_details import _other_client
from test_restaurant_setup import _set_up

AUTH = "/api/v1/auth"
LOGIN = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}


# --- the algorithm -------------------------------------------------------------------

#: RFC 6238 appendix B, SHA-1: the 20-byte ASCII key "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize("unix_time,eight_digits", [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"),
])
def test_codes_match_the_rfc_test_vectors(unix_time, eight_digits):
    # The RFC's codes are 8 digits; ours are the same value cut to 6.
    assert mfa.code_at(RFC_SECRET, unix_time // 30) == eight_digits[-6:]


def test_a_code_is_accepted_a_step_either_side_and_no_further():
    secret = mfa.new_secret()
    now = 1_700_000_000
    step = now // 30
    for offset in (-1, 0, 1):
        assert mfa.matching_step(secret, mfa.code_at(secret, step + offset), now) == step + offset
    assert mfa.matching_step(secret, mfa.code_at(secret, step + 2), now) is None
    assert mfa.matching_step(secret, "12 34 5", now) is None


def test_secrets_are_encrypted_and_need_the_same_key(monkeypatch):
    stored = mfa.encrypt("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DP" not in stored
    assert mfa.decrypt(stored) == "JBSWY3DPEHPK3PXP"
    monkeypatch.setattr("app.config.settings.secret_key", "a-different-key")
    assert mfa.decrypt(stored) is None


def test_backup_codes_work_once_and_forgive_formatting():
    codes, hashes = mfa.new_backup_codes()
    assert len(codes) == 10 and codes[0] not in hashes
    remaining = mfa.use_backup_code(hashes, codes[3].upper().replace("-", " "))
    assert remaining is not None and len(remaining) == 9
    assert mfa.use_backup_code(remaining, codes[3]) is None


# --- setting it up -------------------------------------------------------------------


def _enable(client) -> tuple[str, list[str]]:
    """Set two-step up for the signed-in account; returns (secret, backup codes)."""
    setup = client.post(f"{AUTH}/mfa/setup", json={}).json()
    assert setup["qr_svg"].startswith("data:image/svg+xml")
    assert setup["otpauth_uri"].startswith("otpauth://totp/FullHouse:")
    secret = setup["secret"]
    enabled = client.post(f"{AUTH}/mfa/enable", json={"code": mfa.code_at(secret, mfa.current_step())})
    assert enabled.status_code == 200, enabled.text
    return secret, enabled.json()["codes"]


def _next_code(secret: str) -> str:
    """A code the server hasn't seen yet: the one for the next step (within the drift allowance)."""
    return mfa.code_at(secret, mfa.current_step() + 1)


def test_setting_it_up_proves_the_scan_before_turning_it_on(client, db):
    _set_up(client, db)
    setup = client.post(f"{AUTH}/mfa/setup", json={}).json()
    assert client.post(f"{AUTH}/mfa/enable", json={"code": "000000"}).status_code == 422
    assert client.get(f"{AUTH}/me").json()["mfa_enabled"] is False

    codes = client.post(f"{AUTH}/mfa/enable", json={"code": mfa.code_at(setup["secret"], mfa.current_step())}).json()
    me = client.get(f"{AUTH}/me").json()
    assert (me["mfa_enabled"], me["mfa_backup_codes_left"], len(codes["codes"])) == (True, 10, 10)

    user = db.query(User).filter(User.email == TEST_USER_EMAIL).one()
    db.refresh(user)
    assert setup["secret"] not in (user.mfa_secret or "")  # stored encrypted
    assert "mfa_enabled" in _types(db, TEST_USER_EMAIL)


def test_changing_it_once_on_takes_a_current_code(client, db):
    _set_up(client, db)
    secret, _ = _enable(client)
    assert client.post(f"{AUTH}/mfa/setup", json={}).status_code == 403
    assert client.post(f"{AUTH}/mfa/backup-codes", json={"code": "000000"}).status_code == 403
    assert client.post(f"{AUTH}/mfa/backup-codes", json={"code": _next_code(secret)}).status_code == 200


# --- signing in with it -----------------------------------------------------------------


def test_the_password_alone_no_longer_signs_in(client, db):
    _set_up(client, db)
    secret, _ = _enable(client)

    browser = _browser()
    first = browser.post(f"{AUTH}/login", json=LOGIN)
    assert first.json() == {"mfa_required": True}
    assert browser.get(f"{AUTH}/me").status_code == 401  # no session yet

    assert browser.post(f"{AUTH}/login/mfa", json={"code": "000000"}).status_code == 401
    second = browser.post(f"{AUTH}/login/mfa", json={"code": _next_code(secret)})
    assert second.status_code == 200, second.text
    assert browser.get(f"{AUTH}/me").json()["email"] == TEST_USER_EMAIL

    # The same code can't be used again.
    again = _browser()
    again.post(f"{AUTH}/login", json=LOGIN)
    assert again.post(f"{AUTH}/login/mfa", json={"code": _next_code(secret)}).status_code == 401
    assert _types(db, TEST_USER_EMAIL).count("mfa_failed") == 2


def test_a_backup_code_signs_in_once(client, db):
    _set_up(client, db)
    _, codes = _enable(client)
    browser = _browser()
    browser.post(f"{AUTH}/login", json=LOGIN)
    assert browser.post(f"{AUTH}/login/mfa", json={"code": codes[0]}).status_code == 200
    assert browser.get(f"{AUTH}/me").json()["mfa_backup_codes_left"] == 9

    reused = _browser()
    reused.post(f"{AUTH}/login", json=LOGIN)
    assert reused.post(f"{AUTH}/login/mfa", json={"code": codes[0]}).status_code == 401
    assert "mfa_backup_code_used" in _types(db, TEST_USER_EMAIL)


def test_five_wrong_codes_end_the_attempt(client, db):
    _set_up(client, db)
    secret, _ = _enable(client)
    browser = _browser()
    browser.post(f"{AUTH}/login", json=LOGIN)
    for _ in range(5):
        wrong = browser.post(f"{AUTH}/login/mfa", json={"code": "000000"})
    assert "Sign in again" in wrong.json()["detail"]
    assert browser.post(f"{AUTH}/login/mfa", json={"code": _next_code(secret)}).status_code == 401


def test_a_code_without_a_password_first_gets_nowhere(anon_client):
    assert anon_client.post(f"{AUTH}/login/mfa", json={"code": "123456"}).status_code == 401


# --- required for owners and admins ------------------------------------------------------


def test_owners_and_admins_must_set_it_up_before_anything_else(client, db, monkeypatch):
    restaurant = _set_up(client, db)
    _teammate(db, restaurant["id"], "mo@example.com", MEMBER)
    _teammate(db, restaurant["id"], "admin@example.com", ADMIN)
    monkeypatch.setattr("app.config.settings.require_mfa", True)

    me = client.get(f"{AUTH}/me").json()
    assert me["mfa_setup_required"] is True
    blocked = client.get("/api/v1/restaurant/inventory-items")
    assert blocked.status_code == 403 and "two-step" in blocked.json()["detail"]
    assert _other_client("admin@example.com").get("/api/v1/restaurant/staff").status_code == 403
    # Members may use it, but don't have to.
    assert _other_client("mo@example.com").get("/api/v1/restaurant/staff").status_code == 200

    _enable(client)
    assert client.get("/api/v1/restaurant/inventory-items").status_code == 200
    # And they can't turn it back off.
    assert client.post(f"{AUTH}/mfa/disable", json={"code": "000000"}).status_code == 403


def test_the_demo_never_requires_it(client, db, monkeypatch):
    _set_up(client, db)
    monkeypatch.setattr("app.config.settings.require_mfa", True)
    monkeypatch.setattr("app.config.settings.demo_mode", True)
    assert client.get(f"{AUTH}/me").json()["mfa_setup_required"] is False


def test_an_owner_resets_a_lost_phone_for_an_admin(client, db):
    restaurant = _set_up(client, db)
    _teammate(db, restaurant["id"], "admin@example.com", ADMIN)
    admin = _other_client("admin@example.com")
    _enable(admin)
    admin_id = admin.get(f"{AUTH}/me").json()["id"]

    reset = client.post(f"{AUTH}/team/{admin_id}/reset-mfa")
    assert reset.status_code == 200 and reset.json()["mfa_enabled"] is False
    assert admin.get(f"{AUTH}/me").status_code == 401  # signed out, to set it up again
    # An admin can't do the same to the owner.
    owner_id = client.get(f"{AUTH}/me").json()["id"]
    assert _other_client("admin@example.com").post(f"{AUTH}/team/{owner_id}/reset-mfa").status_code == 403


def test_a_member_may_turn_it_on_and_off(client, db):
    restaurant = _set_up(client, db)
    _teammate(db, restaurant["id"], "mo@example.com", MEMBER)
    member = _other_client("mo@example.com")
    secret, _ = _enable(member)
    assert member.post(f"{AUTH}/mfa/disable", json={"code": _next_code(secret)}).json()["mfa_enabled"] is False


def test_the_command_line_clears_a_locked_out_owners_two_step(client, db, monkeypatch):
    import scripts.reset_mfa as script

    _set_up(client, db)
    _enable(client)
    monkeypatch.setattr(script, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    assert script.main(TEST_USER_EMAIL) == 0
    assert client.get(f"{AUTH}/me").status_code == 401
    browser = _browser()
    assert browser.post(f"{AUTH}/login", json=LOGIN).json()["mfa_enabled"] is False
