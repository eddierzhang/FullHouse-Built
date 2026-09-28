from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.auth.models import User
from app.restaurant.models import Restaurant, Shift, Staff

DETAILS = {
    "name": "Casa Luna", "street": "1 Harbor Way", "city": "Portland", "region": "OR",
    "postal_code": "97201", "country": "United States", "phone": "+1 503 555 0100",
    "email": "Hello@CasaLuna.example.com", "website": "",
}
WEEK = {
    "mon": {"closed": True},
    **{day: {"closed": False, "open": "11:00", "close": "22:00"} for day in ("tue", "wed", "thu", "sun")},
    "fri": {"closed": False, "open": "11:00", "close": "01:00"},  # past midnight
    "sat": {"closed": False, "open": "10:00", "close": "01:00"},
}
HOURS = {"timezone": "America/Los_Angeles", "opening_hours": WEEK}
INVENTORY = {"safety_days": 2, "default_lead_time_days": 4, "daily_stock_check": "7:30"}
STAFF = {"staff_roles": ["Chef", "Server", " server ", "Host", ""]}
SCHEDULING = {"max_shift_hours": 8, "min_rest_hours": 10, "max_weekly_hours": 20, "min_staff_per_open_day": 2}

STEPS = [("details", DETAILS), ("hours", HOURS), ("inventory", INVENTORY), ("staff", STAFF), ("scheduling", SCHEDULING)]
BASE = "/api/v1/restaurant-profile"


def _set_up(client, db):
    from app.agents.models import AgentDefinition
    from app.agents.definitions import AGENT_DEF

    db.add(AgentDefinition(role="main", model="m", enabled=True, **AGENT_DEF))
    db.commit()
    for section, body in STEPS:
        assert client.put(f"{BASE}/{section}", json=body).status_code == 200, section
    response = client.post(f"{BASE}/complete")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_new_account_is_an_owner_with_nothing_set_up(client):
    me = client.get("/api/v1/auth/me").json()
    assert me["role"] == "owner"
    assert me["restaurant_id"] is None
    assert client.get(BASE).json() is None


def test_the_first_step_creates_the_restaurant_and_links_the_owner(client, db):
    response = client.put(f"{BASE}/details", json=DETAILS)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Casa Luna"
    assert body["email"] == "hello@casaluna.example.com"
    assert body["website"] is None
    assert body["completed_steps"] == ["details"]
    assert body["setup_completed_at"] is None

    assert client.get("/api/v1/auth/me").json()["restaurant_id"] == body["id"]
    assert db.query(Restaurant).count() == 1


def test_setup_resumes_where_it_was_left(client):
    client.put(f"{BASE}/details", json=DETAILS)
    client.put(f"{BASE}/hours", json=HOURS)

    # A new session -- as if the owner closed the tab and came back.
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": "eddie@example.com", "password": "correct-horse"})

    saved = client.get(BASE).json()
    assert saved["completed_steps"] == ["details", "hours"]
    assert saved["timezone"] == "America/Los_Angeles"
    assert saved["opening_hours"]["mon"] == {"closed": True, "open": None, "close": None}
    assert saved["opening_hours"]["fri"]["close"] == "01:00"


def test_finishing_needs_every_step(client):
    client.put(f"{BASE}/details", json=DETAILS)
    response = client.post(f"{BASE}/complete")
    assert response.status_code == 409
    assert "hours, inventory, staff, scheduling" in response.json()["detail"]


@pytest.mark.parametrize(
    "section,body",
    [
        ("details", {**DETAILS, "name": "  "}),
        ("details", {**DETAILS, "phone": "call us"}),
        ("details", {**DETAILS, "email": "nope"}),
        ("hours", {**HOURS, "timezone": "Mars/Olympus_Mons"}),
        ("hours", {**HOURS, "opening_hours": {d: {"closed": True} for d in WEEK}}),
        ("hours", {**HOURS, "opening_hours": {**WEEK, "tue": {"closed": False, "open": "11:00"}}}),
        ("hours", {**HOURS, "opening_hours": {k: v for k, v in WEEK.items() if k != "sun"}}),
        ("hours", {**HOURS, "opening_hours": {**WEEK, "tue": {"closed": False, "open": "25:00", "close": "22:00"}}}),
        ("inventory", {**INVENTORY, "safety_days": -1}),
        ("staff", {"staff_roles": ["  "]}),
        ("scheduling", {**SCHEDULING, "max_weekly_hours": 4}),
    ],
)
def test_bad_entries_are_refused(client, section, body):
    assert client.put(f"{BASE}/{section}", json=body).status_code == 422


def test_finishing_sets_up_the_agent(client, db):
    from app.agents import scheduler
    from app.agents.models import AgentDefinition

    restaurant = _set_up(client, db)
    assert restaurant["setup_completed_at"] is not None
    assert restaurant["staff_roles"] == ["Chef", "Server", "Host"]
    assert restaurant["inventory_settings"]["daily_stock_check"] == "07:30"

    maestro = db.query(AgentDefinition).filter_by(key="maestro").one()
    # An hour before the usual 11:00 opening, on the six open days.
    assert maestro.schedule_cron == "0 10 * * tue,wed,thu,fri,sat,sun"
    # The stock check is a job of its own, read from the settings, not a second agent.
    assert scheduler.stock_check_cron(db) == "30 7 * * *"

    definitions = {d["key"]: d for d in client.get("/api/v1/agents/definitions").json()}
    assert list(definitions) == ["maestro"]
    next_review = datetime.fromisoformat(definitions["maestro"]["next_run_at"])
    assert next_review.astimezone(timezone(timedelta(hours=-7))).hour == 10  # 10:00 in Portland (PDT)
    assert client.get("/api/v1/agents/runtime").json()["schedule_timezone"] == "America/Los_Angeles"


def test_finishing_twice_changes_nothing(client, db):
    from app.agents.models import AgentDefinition

    first = _set_up(client, db)
    maestro = db.query(AgentDefinition).filter_by(key="maestro").one()
    maestro.schedule_cron = "15 8 * * *"  # the owner tuned it since
    db.commit()

    again = client.post(f"{BASE}/complete").json()
    assert again["setup_completed_at"] == first["setup_completed_at"]
    db.refresh(maestro)
    assert maestro.schedule_cron == "15 8 * * *"


def test_settings_edits_persist(client, db):
    _set_up(client, db)
    client.put(f"{BASE}/details", json={**DETAILS, "name": "Casa Luna Cantina", "phone": "+1 503 555 0199"})
    client.put(f"{BASE}/staff", json={"staff_roles": ["Chef", "Server", "Host", "Barback"]})

    saved = client.get(BASE).json()
    assert saved["name"] == "Casa Luna Cantina"
    assert saved["phone"] == "+1 503 555 0199"
    assert saved["staff_roles"][-1] == "Barback"
    assert saved["setup_completed_at"] is not None


def test_later_accounts_join_the_same_restaurant_as_members(client, anon_client, db):
    restaurant = _set_up(client, db)
    client.post("/api/v1/auth/logout")

    response = client.post(
        "/api/v1/auth/signup", json={"name": "Second Person", "email": "two@example.com", "password": "another-password"}
    )
    assert response.json()["role"] == "member"
    assert response.json()["restaurant_id"] == restaurant["id"]
    assert db.query(Restaurant).count() == 1
    # Joining isn't taking over: a member can't change what the owner set up.
    assert client.put(f"{BASE}/details", json={**DETAILS, "name": "Taken Over"}).status_code == 403


def test_profile_changes_are_in_the_change_history(client, db, changes):
    client.put(f"{BASE}/details", json=DETAILS)
    (row,) = changes(entity_type="restaurants")
    assert row.operation == "insert"
    assert row.actor_id == "eddie@example.com"


def test_setup_needs_an_owner(client, db):
    user = db.query(User).one()
    user.role = "staff"
    db.commit()
    assert client.put(f"{BASE}/details", json=DETAILS).status_code == 403


# --- the saved details at work --------------------------------------------------


def _person(db, role="Server") -> Staff:
    """On the site's restaurant's team -- or unclaimed, before there is one."""
    restaurant = db.query(Restaurant).order_by(Restaurant.created_at).first()
    person = Staff(name="Ana", role=role, restaurant_id=restaurant.id if restaurant else None)
    db.add(person)
    db.commit()
    return person


def _shift(client, person, day, start, end):
    return client.post("/api/v1/restaurant/shifts", json={
        "staff_id": person.id, "date": day.isoformat(), "start_time": start, "end_time": end, "role": person.role,
    })


def test_no_rules_before_setup(client, db):
    person = _person(db)
    assert _shift(client, person, date(2026, 10, 6), "08:00", "23:00").status_code == 200


def test_shifts_follow_the_scheduling_rules(client, db):
    _set_up(client, db)
    person = _person(db)
    tuesday = date(2026, 10, 6)

    too_long = _shift(client, person, tuesday, "08:00", "17:00")
    assert too_long.status_code == 422
    assert "9 hours; the limit is 8" in too_long.json()["detail"]

    assert _shift(client, person, tuesday, "14:00", "22:00").status_code == 200
    no_rest = _shift(client, person, tuesday + timedelta(days=1), "06:00", "12:00")
    assert no_rest.status_code == 422
    assert "8 hours' rest" in no_rest.json()["detail"]

    overlap = _shift(client, person, tuesday, "20:00", "23:00")
    assert "overlaps" in overlap.json()["detail"]

    assert _shift(client, person, tuesday + timedelta(days=2), "14:00", "22:00").status_code == 200
    over_week = _shift(client, person, tuesday + timedelta(days=4), "14:00", "20:00")
    assert over_week.status_code == 422
    assert "22 hours; the limit is 20" in over_week.json()["detail"]


def test_editing_a_shift_is_checked_without_counting_itself(client, db):
    _set_up(client, db)
    person = _person(db)
    shift = _shift(client, person, date(2026, 10, 6), "14:00", "22:00").json()

    moved = client.patch(f"/api/v1/restaurant/shifts/{shift['id']}", json={"start_time": "13:00", "end_time": "21:00"})
    assert moved.status_code == 200
    stretched = client.patch(f"/api/v1/restaurant/shifts/{shift['id']}", json={"start_time": "09:00"})
    assert stretched.status_code == 422
    db.expire_all()
    assert db.get(Shift, shift["id"]).start_time == time(13, 0)


def test_agent_shift_proposals_follow_the_rules_too(client, db):
    from app.agents.appliers import ApplyError, apply_shift_change

    _set_up(client, db)
    person = _person(db)
    with pytest.raises(ApplyError, match="scheduling rules"):
        apply_shift_change(db, {"staff_id": person.id, "date": "2026-10-06", "start_time": "08:00:00",
                                "end_time": "20:00:00"})
    with pytest.raises(ApplyError, match="isn't one of this restaurant's roles"):
        apply_shift_change(db, {"staff_id": person.id, "date": "2026-10-06", "start_time": "12:00:00",
                                "end_time": "18:00:00", "role": "Sommelier"})


def test_staff_roles_come_from_settings(client, db):
    _set_up(client, db)
    ok = client.post("/api/v1/restaurant/staff", json={"name": "Bo", "role": "server"})
    assert ok.status_code == 200
    refused = client.post("/api/v1/restaurant/staff", json={"name": "Cy", "role": "Sommelier"})
    assert refused.status_code == 422
    assert "Chef, Server, Host" in refused.json()["detail"]


def test_new_suppliers_take_the_default_lead_time(client, db):
    assert client.post("/api/v1/restaurant/suppliers", json={"name": "Before"}).json()["lead_time_days"] == 3
    _set_up(client, db)
    assert client.post("/api/v1/restaurant/suppliers", json={"name": "After"}).json()["lead_time_days"] == 4
    explicit = client.post("/api/v1/restaurant/suppliers", json={"name": "Set", "lead_time_days": 1})
    assert explicit.json()["lead_time_days"] == 1


def test_safety_days_widen_what_counts_as_at_risk(client, db):
    from app.restaurant import crud

    assert crud.supply_chain_summary(db)["safety_days"] == 0
    _set_up(client, db)
    assert crud.supply_chain_summary(db)["safety_days"] == 2


def test_agents_are_told_about_the_restaurant(client, db):
    from app.restaurant.profile import agent_context

    assert agent_context(db) is None
    _set_up(client, db)
    # Friday 23:30 in Portland: open, since Friday runs to 01:00.
    context = agent_context(db, now=datetime(2026, 10, 10, 6, 30, tzinfo=timezone.utc))
    assert "Casa Luna" in context
    assert "Friday 2026-10-09 23:30 (America/Los_Angeles); open right now" in context
    assert "Monday closed" in context
    assert "at most 8 hours" in context
    assert "Staff roles: Chef, Server, Host" in context


@pytest.mark.parametrize(
    "utc,is_open",
    [
        (datetime(2026, 10, 10, 7, 30, tzinfo=timezone.utc), True),   # Sat 00:30 local, Friday's late close
        (datetime(2026, 10, 10, 8, 30, tzinfo=timezone.utc), False),  # Sat 01:30 local
        (datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc), False),   # Monday noon: closed all day
        (datetime(2026, 10, 6, 19, 0, tzinfo=timezone.utc), True),    # Tuesday noon
    ],
)
def test_open_now_follows_local_hours(client, db, utc, is_open):
    from app.restaurant.profile import get_restaurant, is_open_now

    _set_up(client, db)
    assert is_open_now(get_restaurant(db), utc) is is_open


def test_the_demo_restaurant_is_already_set_up(db):
    from app.demo import DEMO_EMAIL, reset_demo_data

    reset_demo_data(db)
    restaurant = db.query(Restaurant).one()
    assert restaurant.setup_completed_at is not None
    assert db.query(User).filter_by(email=DEMO_EMAIL).one().restaurant_id == restaurant.id
