"""The Profile page's Restaurant Details tab: loading, saving, who may, and whose."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth.models import ADMIN, MEMBER, OWNER, User
from app.auth.security import hash_password
from app.restaurant.models import Restaurant
from conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD
from test_restaurant_setup import BASE, DETAILS, HOURS, _set_up

URL = f"{BASE}/restaurant-details"

EDIT = {
    "name": "Casa Luna Cantina",
    "street": "9 Pier Road",
    "city": "Seattle",
    "region": "WA",
    "postal_code": "98101",
    "country": "United States",
    "phone": "+1 206 555 0142",
    "email": "Team@CasaLuna.example.com",
    "website": "casaluna.example.com",
    "timezone": "America/New_York",
    "opening_hours": {
        **{day: {"closed": False, "open": "12:00", "close": "21:00"} for day in ("mon", "tue", "wed", "thu", "sun")},
        "fri": {"closed": False, "open": "12:00", "close": "23:30"},
        "sat": {"closed": True},
    },
}


def _other_client(email: str, password: str = "a-long-password") -> TestClient:
    """A second browser: its own cookies, the same app and test database."""
    from app.main import app

    other = TestClient(app)
    response = other.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return other


def _account(db, email: str, role: str, restaurant_id: str, password: str = "a-long-password") -> User:
    user = User(email=email, name=email.split("@")[0].title(), password_hash=hash_password(password),
                role=role, restaurant_id=restaurant_id)
    db.add(user)
    db.commit()
    return user


def _member(client, db) -> TestClient:
    """Signs up a second account after setup -- which makes it a member."""
    from app.main import app

    member = TestClient(app)
    response = member.post(
        "/api/v1/auth/signup", json={"name": "Mo", "email": "mo@example.com", "password": "a-long-password"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == MEMBER
    return member


# --- loading and saving --------------------------------------------------------


def test_the_tab_loads_what_setup_saved(client, db):
    _set_up(client, db)
    loaded = client.get(BASE).json()

    assert loaded["name"] == DETAILS["name"]
    assert loaded["street"] == DETAILS["street"]
    assert loaded["timezone"] == HOURS["timezone"]
    assert loaded["opening_hours"]["fri"] == {"closed": False, "open": "11:00", "close": "01:00"}
    assert loaded["can_edit"] is True
    assert loaded["updated_by"]["email"] == TEST_USER_EMAIL
    assert loaded["updated_at"]


def test_saving_updates_the_same_record_and_survives_a_refresh(client, db):
    restaurant = _set_up(client, db)

    response = client.put(URL, json=EDIT)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["id"] == restaurant["id"]
    assert saved["email"] == "team@casaluna.example.com"
    assert saved["website"] == "https://casaluna.example.com"
    assert saved["opening_hours"]["sat"] == {"closed": True, "open": None, "close": None}

    # A refresh: a brand-new session reads it back from the database.
    db.expire_all()
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD})
    reloaded = client.get(BASE).json()
    for field in ("name", "street", "city", "phone", "timezone", "website"):
        assert reloaded[field] == saved[field]
    assert reloaded["opening_hours"] == saved["opening_hours"]
    # Edited in place: still one restaurant, and the other settings untouched.
    assert db.query(Restaurant).count() == 1
    assert reloaded["staff_roles"] == restaurant["staff_roles"]
    assert reloaded["scheduling_rules"] == restaurant["scheduling_rules"]


def test_saving_records_when_and_by_whom(client, db):
    before = _set_up(client, db)
    saved = client.put(URL, json=EDIT).json()

    assert saved["updated_at"] > before["updated_at"]
    assert saved["updated_by"] == {
        "id": db.query(User).filter_by(email=TEST_USER_EMAIL).one().id,
        "name": "Eddie",
        "email": TEST_USER_EMAIL,
    }
    row = db.get(Restaurant, saved["id"])
    assert row.updated_by == saved["updated_by"]["id"]


def test_saving_nothing_new_leaves_the_timestamp_alone(client, db):
    _set_up(client, db)
    first = client.put(URL, json=EDIT).json()
    again = client.put(URL, json=EDIT).json()
    assert again["updated_at"] == first["updated_at"]


def test_a_save_based_on_stale_values_is_refused(client, db):
    loaded = _set_up(client, db)
    client.put(URL, json={**EDIT, "name": "Someone Else's Edit"})

    response = client.put(URL, json={**EDIT, "name": "Mine", "expected_updated_at": loaded["updated_at"]})
    assert response.status_code == 409
    assert "Reload" in response.json()["detail"]
    assert client.get(BASE).json()["name"] == "Someone Else's Edit"

    current = client.get(BASE).json()["updated_at"]
    response = client.put(URL, json={**EDIT, "name": "Mine", "expected_updated_at": current})
    assert response.status_code == 200
    assert response.json()["name"] == "Mine"


def test_there_is_nothing_to_edit_before_setup(client):
    assert client.put(URL, json=EDIT).status_code == 404


# --- validation ---------------------------------------------------------------


def _hours(**days):
    week = {day: {"closed": False, "open": "11:00", "close": "22:00"} for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    return {**week, **days}


@pytest.mark.parametrize(
    "change,message",
    [
        ({"name": "   "}, "required"),
        ({"email": "not-an-email"}, "valid email"),
        ({"phone": "call us"}, "phone number"),
        ({"phone": "12"}, "phone number"),
        ({"website": "not a website"}, "web address"),
        ({"website": "ftp://casaluna.example.com"}, "web address"),
        ({"website": "localhost"}, "web address"),
        ({"timezone": "Mars/Olympus_Mons"}, "timezone"),
        ({"opening_hours": _hours(mon={"closed": False, "open": "11:00"})}, "opening and a closing"),
        ({"opening_hours": _hours(mon={"closed": False, "open": "11:00", "close": "11:00"})}, "can't be the same"),
        ({"opening_hours": _hours(mon={"closed": False, "open": "25:00", "close": "22:00"})}, "not a time"),
        ({"opening_hours": {day: {"closed": True} for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}},
         "at least one day"),
        ({"opening_hours": {"mon": {"closed": True}}}, "every day"),
        # Friday runs to 03:00, but Saturday opens at 02:00.
        ({"opening_hours": _hours(fri={"closed": False, "open": "18:00", "close": "03:00"},
                                  sat={"closed": False, "open": "02:00", "close": "10:00"})},
         "Friday's hours run to 03:00, past Saturday's opening at 02:00"),
        # Sunday into Monday wraps around the week.
        ({"opening_hours": _hours(sun={"closed": False, "open": "20:00", "close": "10:00"},
                                  mon={"closed": False, "open": "09:00", "close": "17:00"})},
         "past Monday's opening"),
    ],
)
def test_bad_entries_are_refused(client, db, change, message):
    before = _set_up(client, db)
    response = client.put(URL, json={**EDIT, **change})
    assert response.status_code == 422
    assert message in str(response.json()["detail"])
    after = client.get(BASE).json()
    assert after["name"] == before["name"] and after["updated_at"] == before["updated_at"]


def test_late_hours_that_end_before_the_next_opening_are_fine(client, db):
    _set_up(client, db)
    hours = _hours(fri={"closed": False, "open": "18:00", "close": "02:00"},
                   sat={"closed": False, "open": "02:00", "close": "23:00"})
    assert client.put(URL, json={**EDIT, "opening_hours": hours}).status_code == 200


def test_blank_optional_fields_are_cleared(client, db):
    _set_up(client, db)
    saved = client.put(URL, json={**EDIT, "email": "", "website": "  ", "region": ""}).json()
    assert saved["email"] is None and saved["website"] is None and saved["region"] is None


# --- who may -------------------------------------------------------------------


def test_members_can_read_but_not_edit(client, db):
    _set_up(client, db)
    member = _member(client, db)

    loaded = member.get(BASE).json()
    assert loaded["name"] == DETAILS["name"]
    assert loaded["can_edit"] is False
    assert loaded["updated_by"] is None  # who edited is for owners and admins

    response = member.put(URL, json={**EDIT, "name": "Hijacked"})
    assert response.status_code == 403
    for section, body in (("details", DETAILS), ("hours", HOURS)):
        assert member.put(f"{BASE}/{section}", json=body).status_code == 403
    assert client.get(BASE).json()["name"] == DETAILS["name"]


def test_an_owner_can_make_someone_an_admin_who_can_then_edit(client, db):
    _set_up(client, db)
    member = _member(client, db)
    mo = db.query(User).filter_by(email="mo@example.com").one()

    team = client.get("/api/v1/auth/team").json()
    assert {(p["email"], p["role"]) for p in team} == {(TEST_USER_EMAIL, OWNER), ("mo@example.com", MEMBER)}

    response = client.patch(f"/api/v1/auth/team/{mo.id}", json={"role": ADMIN})
    assert response.status_code == 200
    assert response.json()["role"] == ADMIN

    saved = member.put(URL, json={**EDIT, "name": "Edited By Admin"})
    assert saved.status_code == 200
    assert saved.json()["updated_by"]["email"] == "mo@example.com"
    # The owner sees who it was.
    assert client.get(BASE).json()["updated_by"]["name"] == "Mo"

    # And taking it away takes the access with it.
    client.patch(f"/api/v1/auth/team/{mo.id}", json={"role": MEMBER})
    assert member.put(URL, json=EDIT).status_code == 403


def test_only_an_owner_changes_roles(client, db):
    _set_up(client, db)
    member = _member(client, db)
    owner = db.query(User).filter_by(email=TEST_USER_EMAIL).one()
    mo = db.query(User).filter_by(email="mo@example.com").one()

    assert member.get("/api/v1/auth/team").status_code == 403
    assert member.patch(f"/api/v1/auth/team/{mo.id}", json={"role": ADMIN}).status_code == 403

    mo.role = ADMIN
    db.commit()
    # An admin can see the team but not hand out access.
    assert member.get("/api/v1/auth/team").status_code == 200
    assert member.patch(f"/api/v1/auth/team/{mo.id}", json={"role": MEMBER}).status_code == 403

    assert client.patch(f"/api/v1/auth/team/{owner.id}", json={"role": MEMBER}).status_code == 409
    assert client.patch(f"/api/v1/auth/team/{mo.id}", json={"role": OWNER}).status_code == 422
    assert client.patch("/api/v1/auth/team/nobody", json={"role": ADMIN}).status_code == 404


# --- whose -----------------------------------------------------------------------


def _second_restaurant(db) -> tuple[Restaurant, TestClient]:
    other = Restaurant(name="Other Place", street="2 Elsewhere", city="Denver", country="United States",
                       phone="+1 303 555 0100", timezone="America/Denver",
                       opening_hours=_hours(), completed_steps=["details", "hours"],
                       setup_completed_at=datetime.now(timezone.utc))
    db.add(other)
    db.commit()
    _account(db, "boss@other.example.com", OWNER, other.id)
    return other, _other_client("boss@other.example.com")


def test_edits_never_reach_another_restaurant(client, db):
    mine = _set_up(client, db)
    other, other_owner = _second_restaurant(db)
    other_before = other_owner.get(BASE).json()
    assert other_before["id"] == other.id  # each owner loads their own

    assert client.put(URL, json={**EDIT, "name": "Mine Renamed"}).status_code == 200
    assert other_owner.get(BASE).json() == other_before

    assert other_owner.put(URL, json={**EDIT, "name": "Theirs Renamed"}).status_code == 200
    assert client.get(BASE).json()["name"] == "Mine Renamed"
    assert client.get(BASE).json()["id"] == mine["id"]
    # The setup-step routes are scoped the same way.
    assert other_owner.put(f"{BASE}/details", json={**DETAILS, "name": "Via Setup"}).status_code == 200
    assert client.get(BASE).json()["name"] == "Mine Renamed"

    db.expire_all()
    assert db.get(Restaurant, mine["id"]).name == "Mine Renamed"
    assert db.get(Restaurant, other.id).name == "Via Setup"


def test_another_restaurants_owner_cannot_touch_this_team_or_history(client, db):
    mine = _set_up(client, db)
    client.put(URL, json=EDIT)
    _member(client, db)
    mo = db.query(User).filter_by(email="mo@example.com").one()
    _, other_owner = _second_restaurant(db)

    assert other_owner.patch(f"/api/v1/auth/team/{mo.id}", json={"role": ADMIN}).status_code == 404
    assert {p["email"] for p in other_owner.get("/api/v1/auth/team").json()} == {"boss@other.example.com"}

    history = other_owner.get("/api/v1/changes", params={"entity_type": "restaurants"}).json()
    assert all(item["entity_id"] != mine["id"] for item in history["items"])
    assert other_owner.get(f"/api/v1/changes/entity/restaurants/{mine['id']}").json() == []


# --- history ---------------------------------------------------------------------


def test_history_of_the_details_is_for_owners_and_admins(client, db):
    restaurant = _set_up(client, db)
    client.put(URL, json=EDIT)
    member = _member(client, db)

    owner_view = client.get("/api/v1/changes", params={"entity_type": "restaurants"}).json()
    assert owner_view["total"] > 0
    change_id = owner_view["items"][0]["id"]

    assert member.get("/api/v1/changes", params={"entity_type": "restaurants"}).json()["total"] == 0
    assert member.get(f"/api/v1/changes/{change_id}").status_code == 404
    assert member.get(f"/api/v1/changes/entity/restaurants/{restaurant['id']}").json() == []


def test_details_cant_be_reverted_around_the_profile_page(client, db):
    _set_up(client, db)
    client.put(URL, json=EDIT)
    member = _member(client, db)
    latest = client.get("/api/v1/changes", params={"entity_type": "restaurants"}).json()["items"][0]

    assert member.post(f"/api/v1/changes/{latest['id']}/revert").status_code == 404
    response = client.post(f"/api/v1/changes/{latest['id']}/revert")
    assert response.status_code == 422
    assert "Profile page" in response.json()["detail"]
    assert client.get(BASE).json()["name"] == EDIT["name"]


# --- the rest of the app picks it up ------------------------------------------------


def test_agents_see_saved_details_on_their_next_run(client, db):
    from app.restaurant.profile import agent_context

    _set_up(client, db)
    client.put(URL, json=EDIT)
    # Saturday noon in New York: closed all day now.
    context = agent_context(db, now=datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc))
    assert "Casa Luna Cantina" in context
    assert "9 Pier Road, Seattle, WA, 98101, United States" in context
    assert "(America/New_York); closed right now" in context
    assert "Saturday closed" in context
    assert "Friday 12:00-23:30" in context


def test_agent_schedules_move_to_the_new_timezone(client, db):
    _set_up(client, db)
    assert client.get("/api/v1/agents/runtime").json()["schedule_timezone"] == "America/Los_Angeles"
    client.put(URL, json=EDIT)
    assert client.get("/api/v1/agents/runtime").json()["schedule_timezone"] == "America/New_York"
