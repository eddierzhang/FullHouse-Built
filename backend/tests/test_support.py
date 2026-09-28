"""Getting help, sending feedback, error alerts, and exporting a restaurant's data."""

import csv
import io
import zipfile

import pytest

from app.auth.models import User
from app.support import alerts
from app.support.models import Feedback
from app.support.routes import error_limit, feedback_limit
from conftest import TEST_USER_EMAIL
from test_restaurant_details import _member, _second_restaurant
from test_restaurant_setup import _set_up

SUPPORT = "/api/v1/support"
ITEMS = "/api/v1/restaurant/inventory-items"


@pytest.fixture(autouse=True)
def _fresh_limits():
    feedback_limit.clear()
    error_limit.clear()


def test_the_help_page_shows_the_configured_contact(client, db, monkeypatch):
    _set_up(client, db)
    assert client.get(SUPPORT).json() == {"support_email": None, "support_url": None}
    monkeypatch.setattr("app.config.settings.support_email", "help@fullhouse.example")
    assert client.get(SUPPORT).json()["support_email"] == "help@fullhouse.example"


# --- feedback ---------------------------------------------------------------------------


def test_anyone_can_send_feedback_and_managers_read_it(client, db):
    restaurant = _set_up(client, db)
    member = _member(client, db)
    sent = member.post(f"{SUPPORT}/feedback", json={"message": "  The count screen is confusing  ", "page": "inventory"})
    assert sent.status_code == 201
    assert sent.json()["message"] == "The count screen is confusing"

    stored = db.query(Feedback).one()
    assert (stored.restaurant_id, stored.sent_by, stored.page) == (restaurant["id"], "mo@example.com", "inventory")
    assert [f["message"] for f in client.get(f"{SUPPORT}/feedback").json()] == ["The count screen is confusing"]
    assert member.get(f"{SUPPORT}/feedback").status_code == 403


def test_feedback_is_kept_to_its_restaurant(client, db):
    _set_up(client, db)
    client.post(f"{SUPPORT}/feedback", json={"message": "ours"})
    _, other = _second_restaurant(db)
    other.post(f"{SUPPORT}/feedback", json={"message": "theirs"})
    assert [f["message"] for f in client.get(f"{SUPPORT}/feedback").json()] == ["ours"]


def test_feedback_needs_words_and_has_limits(client, db):
    _set_up(client, db)
    assert client.post(f"{SUPPORT}/feedback", json={"message": "   "}).status_code == 422
    assert client.post(f"{SUPPORT}/feedback", json={"message": "x" * 2001}).status_code == 422
    for _ in range(10):
        assert client.post(f"{SUPPORT}/feedback", json={"message": "again"}).status_code == 201
    assert client.post(f"{SUPPORT}/feedback", json={"message": "again"}).status_code == 429


def test_a_one_time_link_never_lands_in_feedback(client, db):
    _set_up(client, db)
    client.post(f"{SUPPORT}/feedback", json={"message": "stuck", "page": "invite/SECRET-TOKEN-123"})
    assert db.query(Feedback).one().page == "invite/[token]"


def test_signing_in_is_needed(anon_client):
    assert anon_client.post(f"{SUPPORT}/feedback", json={"message": "hi"}).status_code == 401
    assert anon_client.post(f"{SUPPORT}/client-errors", json={"message": "boom"}).status_code == 401


# --- browser errors ------------------------------------------------------------------------


def test_browser_errors_are_reported_without_tokens(client, db, monkeypatch):
    _set_up(client, db)
    reports = []
    monkeypatch.setattr("app.support.routes.report", lambda message, **context: reports.append((message, context)))

    response = client.post(f"{SUPPORT}/client-errors", json={
        "message": "TypeError: x is undefined",
        "stack": "at render (http://app.example/#reset/LIVE-TOKEN?key=abc:10:3)",
        "page": "reset/LIVE-TOKEN",
    })
    assert response.status_code == 204
    (message, context), = reports
    assert message == "Browser error: TypeError: x is undefined"
    assert "LIVE-TOKEN" not in str(context) and "key=abc" not in str(context)


def test_a_looping_browser_error_is_dropped_past_the_limit(client, db, monkeypatch):
    _set_up(client, db)
    reports = []
    monkeypatch.setattr("app.support.routes.report", lambda message, **context: reports.append(message))
    for _ in range(40):
        assert client.post(f"{SUPPORT}/client-errors", json={"message": "loop"}).status_code == 204
    assert len(reports) == 30


# --- alerts --------------------------------------------------------------------------------


def test_sentry_stays_off_without_a_dsn(monkeypatch):
    monkeypatch.setattr("app.config.settings.sentry_dsn", None)
    monkeypatch.setattr(alerts, "_configured", False)
    assert alerts.configure_sentry() is False


def test_what_goes_to_sentry_is_scrubbed():
    event = {
        "request": {
            "url": "https://app.example/api/v1/auth/invite/LIVE-TOKEN?x=1",
            "query_string": "x=1",
            "cookies": {"fullhouse_session": "abc"},
            "data": {"password": "hunter2", "code": "123456"},
            "headers": {"Cookie": "a=b", "Authorization": "Bearer t", "Idempotency-Key": "k", "User-Agent": "Firefox"},
        },
        "transaction": "/api/v1/auth/reset/OTHER-TOKEN",
        "breadcrumbs": {"values": [{"data": {"url": "https://generativelanguage.example/v1?key=SECRET-KEY", "http.query": "key=SECRET-KEY"}}]},
        "user": {"id": "u1", "email": "someone@example.com", "ip_address": "1.2.3.4"},
    }
    cleaned = alerts._scrub(event, {})
    text = str(cleaned)
    for secret in ("LIVE-TOKEN", "OTHER-TOKEN", "hunter2", "123456", "SECRET-KEY", "someone@example.com", "1.2.3.4", "Bearer t"):
        assert secret not in text
    assert cleaned["request"]["headers"]["User-Agent"] == "Firefox"
    assert cleaned["user"] == {"id": "u1"}


# --- export ---------------------------------------------------------------------------------


def _unzip(response) -> dict[str, list[dict]]:
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return {
        name: list(csv.DictReader(io.StringIO(archive.read(name).decode())))
        for name in archive.namelist()
        if name.endswith(".csv")
    }


def test_owners_can_export_everything_the_restaurant_keeps(client, db):
    _set_up(client, db)
    client.post(ITEMS, json={"name": "Flour", "unit": "lb", "quantity_on_hand": 5, "reorder_threshold": 1})

    response = client.get(f"{SUPPORT}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    files = _unzip(response)

    assert {"restaurant.csv", "inventory_items.csv", "stock_history.csv", "suppliers.csv", "menu_items.csv",
            "staff.csv", "shifts.csv", "orders.csv", "change_history.csv", "team.csv"} <= set(files)
    assert [row["name"] for row in files["inventory_items.csv"]] == ["Flour"]
    assert files["stock_history.csv"][0]["reason"] == "initial"
    assert files["team.csv"][0]["email"] == TEST_USER_EMAIL


def test_the_export_never_holds_secrets(client, db):
    _set_up(client, db)
    user = db.query(User).one()
    raw = zipfile.ZipFile(io.BytesIO(client.get(f"{SUPPORT}/export").content))
    everything = "".join(raw.read(name).decode() for name in raw.namelist())
    assert user.password_hash not in everything
    assert "password_hash" not in everything and "mfa_secret" not in everything


def test_the_export_is_only_this_restaurant(client, db):
    _set_up(client, db)
    client.post(ITEMS, json={"name": "Our Flour", "unit": "lb", "quantity_on_hand": 5, "reorder_threshold": 1})
    _, other = _second_restaurant(db)
    other.post(ITEMS, json={"name": "Their Flour", "unit": "lb", "quantity_on_hand": 5, "reorder_threshold": 1})

    raw = zipfile.ZipFile(io.BytesIO(client.get(f"{SUPPORT}/export").content))
    everything = "".join(raw.read(name).decode() for name in raw.namelist())
    assert "Our Flour" in everything
    assert "Their Flour" not in everything and "boss@other.example.com" not in everything


def test_spreadsheet_formulas_in_the_data_cant_run(client, db):
    _set_up(client, db)
    client.post(ITEMS, json={"name": "=HYPERLINK(\"http://evil\")", "unit": "lb", "quantity_on_hand": 1, "reorder_threshold": 0})
    files = _unzip(client.get(f"{SUPPORT}/export"))
    assert files["inventory_items.csv"][0]["name"].startswith("'=")


def test_exports_are_for_managers_and_are_logged(client, db):
    from app.auth.models import SecurityEvent

    _set_up(client, db)
    member = _member(client, db)
    assert member.get(f"{SUPPORT}/export").status_code == 403
    client.get(f"{SUPPORT}/export")
    (event,) = db.query(SecurityEvent).filter_by(type="data_exported").all()
    assert event.detail["files"] >= 10


def test_a_dsn_turns_alerts_on_with_nothing_personal(monkeypatch):
    import sentry_sdk

    seen = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr("app.config.settings.sentry_dsn", "https://key@o0.ingest.example/1")
    monkeypatch.setattr(alerts, "_configured", False)

    assert alerts.configure_sentry() is True
    assert seen["send_default_pii"] is False
    assert seen["before_send"] is alerts._scrub
    assert seen["traces_sample_rate"] == 0.0
    # Errors are named by route template, never by a URL that might hold a token.
    assert all(getattr(i, "transaction_style", "url") == "url" for i in seen["integrations"])
    monkeypatch.setattr(alerts, "_configured", False)
