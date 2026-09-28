"""HR, phase one: profiles, who may see and change them, and published schedules."""

from datetime import date, time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.agents.appliers import apply_shift_change
from app.restaurant.models import Shift, Staff
from conftest import TEST_USER_EMAIL
from test_restaurant_details import _member, _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/restaurant"
MO = "mo@example.com"  # the member _member signs up
TUESDAY = date(2026, 10, 6)


def _hire(client, **fields):
    body = {"name": "Mo Diaz", "role": "Server", "email": MO, "phone": "555-0101",
            "emergency_contact": "Sam Diaz 555-0199", "hire_date": "2026-03-01", "notes": "Wants weekends off", **fields}
    response = client.post(f"{API}/staff", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _shift(client, person_id, day=TUESDAY, start="17:00", end="22:00"):
    return client.post(f"{API}/shifts", json={
        "staff_id": person_id, "date": day.isoformat(), "start_time": start, "end_time": end, "role": "Server",
    })


# --- who may change what -----------------------------------------------------------


def test_members_cannot_manage_the_team_or_the_schedule(client, db):
    _set_up(client, db)
    mo = _hire(client)
    shift = _shift(client, mo["id"]).json()
    member = _member(client, db)

    refused = [
        member.post(f"{API}/staff", json={"name": "X", "role": "Server"}),
        member.delete(f"{API}/staff/{mo['id']}"),
        _shift(member, mo["id"], day=date(2026, 10, 8)),
        member.patch(f"{API}/shifts/{shift['id']}", json={"start_time": "18:00"}),
        member.delete(f"{API}/shifts/{shift['id']}"),
        member.post(f"{API}/shifts/publish"),
    ]
    assert [r.status_code for r in refused] == [403] * len(refused)


def test_an_employee_may_update_only_their_own_contact_details(client, db):
    _set_up(client, db)
    mo = _hire(client)
    other = _hire(client, name="Ana", email="ana@example.com")
    member = _member(client, db)

    ok = member.patch(f"{API}/staff/{mo['id']}", json={"phone": " 555-0202 ", "emergency_contact": "Lee 555-0300"})
    assert ok.status_code == 200, ok.text
    assert (ok.json()["phone"], ok.json()["emergency_contact"]) == ("555-0202", "Lee 555-0300")

    assert member.patch(f"{API}/staff/{mo['id']}", json={"role": "Chef"}).status_code == 403
    assert member.patch(f"{API}/staff/{mo['id']}", json={"notes": "promote me"}).status_code == 403
    assert member.patch(f"{API}/staff/{other['id']}", json={"phone": "1"}).status_code == 403


# --- who sees what -------------------------------------------------------------------


def test_coworkers_see_names_and_roles_and_managers_see_everything(client, db):
    _set_up(client, db)
    _hire(client)
    _hire(client, name="Ana", email="ana@example.com", phone="555-0111", notes="Keyholder")
    member = _member(client, db)

    as_member = {p["name"]: p for p in member.get(f"{API}/staff").json()}
    ana, me = as_member["Ana"], as_member["Mo Diaz"]
    assert (ana["is_me"], ana["email"], ana["phone"], ana["notes"], ana["has_account"]) == (False, None, None, None, None)
    # Their own profile, but never the manager's notes about them.
    assert (me["is_me"], me["email"], me["phone"], me["hire_date"]) == (True, MO, "555-0101", "2026-03-01")
    assert me["notes"] is None and me["has_account"] is None

    as_owner = {p["name"]: p for p in client.get(f"{API}/staff").json()}
    assert as_owner["Ana"]["phone"] == "555-0111" and as_owner["Ana"]["notes"] == "Keyholder"
    # Mo has signed up with the address on their profile; Ana hasn't.
    assert (as_owner["Mo Diaz"]["has_account"], as_owner["Ana"]["has_account"]) == (True, False)
    assert not any(p["is_me"] for p in as_owner.values())


def test_the_login_is_linked_by_email_whatever_its_case(client, db):
    _set_up(client, db)
    _hire(client, email="  MO@Example.COM ")
    member = _member(client, db)

    (me,) = member.get(f"{API}/staff").json()
    assert me["is_me"] and me["email"] == MO


def test_two_profiles_cannot_share_an_email(client, db):
    _set_up(client, db)
    mo = _hire(client)
    ana = _hire(client, name="Ana", email="ana@example.com")

    assert client.post(f"{API}/staff", json={"name": "Twin", "role": "Server", "email": "MO@example.com"}).status_code == 409
    assert client.patch(f"{API}/staff/{ana['id']}", json={"email": MO}).status_code == 409
    assert client.patch(f"{API}/staff/{mo['id']}", json={"email": MO}).status_code == 200  # keeping your own is fine


# --- publishing ----------------------------------------------------------------------


def test_drafts_are_for_managers_until_published(client, db):
    _set_up(client, db)
    mo = _hire(client)
    member = _member(client, db)

    draft = _shift(client, mo["id"])
    assert draft.status_code == 200, draft.text
    assert draft.json()["published_at"] is None
    assert [s["id"] for s in client.get(f"{API}/shifts").json()] == [draft.json()["id"]]
    assert member.get(f"{API}/shifts").json() == []

    assert client.post(f"{API}/shifts/publish").json() == {"published": 1}
    (seen,) = member.get(f"{API}/shifts").json()
    assert seen["id"] == draft.json()["id"] and seen["published_at"] is not None

    # Nothing left to publish; an edit to a published shift shows straight away.
    assert client.post(f"{API}/shifts/publish").json() == {"published": 0}
    assert client.patch(f"{API}/shifts/{seen['id']}", json={"start_time": "18:00"}).status_code == 200
    (edited,) = member.get(f"{API}/shifts").json()
    assert edited["start_time"] == "18:00:00" and edited["published_at"] is not None


def test_an_approved_agent_proposal_joins_the_drafts(client, db):
    restaurant = _set_up(client, db)
    mo = _hire(client)

    apply_shift_change(db, {"staff_id": mo["id"], "date": TUESDAY.isoformat(), "start_time": "17:00",
                            "end_time": "22:00", "role": "Server"})
    db.commit()

    (shift,) = db.query(Shift).all()
    assert shift.restaurant_id == restaurant["id"] and shift.published_at is None


# --- whose ---------------------------------------------------------------------------


def test_another_restaurants_team_and_schedule_read_as_missing(client, db):
    _set_up(client, db)
    mo = _hire(client)
    shift = _shift(client, mo["id"]).json()
    other, other_owner = _second_restaurant(db)

    assert other_owner.get(f"{API}/staff").json() == []
    assert other_owner.get(f"{API}/shifts").json() == []
    assert other_owner.patch(f"{API}/staff/{mo['id']}", json={"name": "Hijacked"}).status_code == 404
    assert other_owner.delete(f"{API}/shifts/{shift['id']}").status_code == 404
    assert _shift(other_owner, mo["id"]).status_code == 404
    assert other_owner.post(f"{API}/shifts/publish").json() == {"published": 0}

    db.expire_all()
    assert db.get(Staff, mo["id"]).name == "Mo Diaz"
    assert db.get(Shift, shift["id"]).published_at is None


def test_a_team_seeded_before_setup_is_claimed_by_the_restaurant(client, db):
    person = Staff(name="Early Bird", role="Server")
    db.add(person)
    db.flush()
    db.add(Shift(staff_id=person.id, date=TUESDAY, start_time=time(9), end_time=time(12), role="Server"))
    db.commit()

    restaurant = _set_up(client, db)

    assert [p["name"] for p in client.get(f"{API}/staff").json()] == ["Early Bird"]
    db.expire_all()
    assert db.query(Shift).one().restaurant_id == restaurant["id"]


# --- the migration, on a database that already has a team ---------------------------

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def test_the_migration_keeps_the_team_and_what_staff_could_already_see(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'populated.db'}"
    monkeypatch.setattr("app.config.settings.database_url", db_url)
    config = _alembic_config(db_url)
    command.upgrade(config, "f2a9d5c3b7e1")  # the revision before HR

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO restaurants (id, name, timezone, completed_steps, created_at, updated_at) "
            "VALUES ('r1', 'Casa', 'UTC', '[]', '2026-09-01 00:00:00', '2026-09-01 00:00:00')"
        ))
        conn.execute(text("INSERT INTO staff (id, name, role, email) VALUES ('s1', 'Mo', 'Server', 'mo@example.com')"))
        conn.execute(text(
            "INSERT INTO shifts (staff_id, date, start_time, end_time, role) "
            "VALUES ('s1', '2026-10-06', '17:00:00', '22:00:00', 'Server')"
        ))

    command.upgrade(config, "head")

    with engine.connect() as conn:
        staff = conn.execute(text("SELECT restaurant_id, name, email, phone FROM staff")).one()
        shift = conn.execute(text("SELECT restaurant_id, published_at FROM shifts")).one()
    assert tuple(staff) == ("r1", "Mo", "mo@example.com", None)
    # Staff could see this shift before; publishing it keeps it in their view.
    assert shift.restaurant_id == "r1" and shift.published_at is not None

    command.downgrade(config, "f2a9d5c3b7e1")
    with engine.connect() as conn:
        staff_columns = {c[1] for c in conn.execute(text("PRAGMA table_info(staff)"))}
        shift_columns = {c[1] for c in conn.execute(text("PRAGMA table_info(shifts)"))}
        assert conn.execute(text("SELECT name FROM staff")).scalar() == "Mo"
    assert staff_columns == {"id", "name", "role", "email"}
    assert "published_at" not in shift_columns and "restaurant_id" not in shift_columns
