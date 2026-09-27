"""Scheduled agents and promotions that end on their own."""

from datetime import datetime, timedelta, timezone

import pytest

from app.agents import executor, scheduler
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.restaurant.models import MenuItem


@pytest.fixture
def submitted(monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(executor, "submit", queued.append)
    return queued


def _agent(db, key="inventory", cron=None, enabled=True):
    defn = AgentDefinition(
        key=key, name=key.title(), description="d", role="subagent", system_prompt="s",
        schedule_cron=cron, enabled=enabled,
    )
    db.add(defn)
    db.commit()
    return defn


# --- cron parsing -----------------------------------------------------------


def test_a_valid_crontab_computes_its_next_fire_time():
    now = datetime(2026, 9, 12, 8, 30, tzinfo=timezone.utc)

    assert scheduler.next_fire_time("0 9 * * *", now) == datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
    assert scheduler.next_fire_time("0 9 * * *", now.replace(hour=10)) == datetime(
        2026, 9, 13, 9, 0, tzinfo=timezone.utc
    )


def test_no_schedule_has_no_next_fire_time():
    assert scheduler.next_fire_time(None) is None


@pytest.mark.parametrize("bad", ["every morning", "0 9 * *", "61 * * * *", ""])
def test_invalid_crontabs_are_rejected(bad):
    with pytest.raises(scheduler.InvalidSchedule):
        scheduler.parse_cron(bad)


# --- the schedule API -------------------------------------------------------


def test_scheduling_an_agent_reports_when_it_will_next_run(client, db):
    _agent(db)

    body = client.patch("/api/v1/agents/definitions/inventory", json={"schedule_cron": "0 9 * * *"}).json()

    assert body["schedule_cron"] == "0 9 * * *"
    assert body["next_run_at"] is not None


def test_an_invalid_schedule_is_refused_and_not_saved(client, db):
    defn = _agent(db)

    response = client.patch("/api/v1/agents/definitions/inventory", json={"schedule_cron": "whenever"})

    assert response.status_code == 422
    assert "not a valid cron schedule" in response.json()["detail"]
    db.refresh(defn)
    assert defn.schedule_cron is None


def test_clearing_a_schedule(client, db):
    _agent(db, cron="0 9 * * *")

    body = client.patch("/api/v1/agents/definitions/inventory", json={"schedule_cron": None}).json()

    assert body["schedule_cron"] is None
    assert body["next_run_at"] is None


def test_a_paused_agent_has_no_next_run(client, db):
    _agent(db, cron="0 9 * * *")

    body = client.patch("/api/v1/agents/definitions/inventory", json={"enabled": False}).json()

    assert body["enabled"] is False
    assert body["next_run_at"] is None


def test_scheduling_an_unknown_agent_is_404(client, db):
    assert client.patch("/api/v1/agents/definitions/ghost", json={"enabled": True}).status_code == 404


# --- firing -----------------------------------------------------------------


def test_a_scheduled_fire_queues_a_run(db, submitted):
    _agent(db, cron="0 9 * * *")

    run_id = scheduler.run_scheduled_agent("inventory", db)

    run = db.get(AgentRun, run_id)
    assert run.trigger_type == "scheduled"
    assert run.status == "queued"
    assert submitted == [run_id]


def test_a_fire_is_skipped_while_a_run_is_still_in_flight(db, submitted):
    """A slow model on a frequent schedule must not pile up runs."""
    defn = _agent(db, cron="* * * * *")
    db.add(AgentRun(agent_definition_id=defn.id, status="running"))
    db.commit()

    assert scheduler.run_scheduled_agent("inventory", db) is None
    assert submitted == []


def test_a_disabled_or_unscheduled_agent_does_not_fire(db, submitted):
    _agent(db, key="paused", cron="0 9 * * *", enabled=False)
    _agent(db, key="unscheduled", cron=None)

    assert scheduler.run_scheduled_agent("paused", db) is None
    assert scheduler.run_scheduled_agent("unscheduled", db) is None
    assert submitted == []


# --- promotions -------------------------------------------------------------


def _dish(db, price=12.0):
    item = MenuItem(name="Garlic Bread", category="Appetizer", price=price, cost=1.0)
    db.add(item)
    db.commit()
    return item


def _promotion(db, item, payload_extra):
    defn = _agent(db, key="marketing")
    run = AgentRun(agent_definition_id=defn.id, status="succeeded")
    db.add(run)
    db.commit()
    action = AgentAction(
        run_id=run.id, agent_definition_id=defn.id, action_type="menu_change",
        payload={"menu_item_id": item.id, "change_type": "promotion", "details": "BOGO", **payload_extra},
    )
    db.add(action)
    db.commit()
    return action


def test_a_timed_promotion_remembers_the_regular_price(client, db):
    item = _dish(db, price=12.0)
    ends = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    action = _promotion(db, item, {"new_price": 8.0, "promo_ends_at": ends})

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")

    assert response.status_code == 200
    assert "until" in response.json()["applied_result"]
    db.refresh(item)
    assert item.price == 8.0
    assert item.regular_price == 12.0
    assert item.promo_ends_at is not None


def test_the_end_time_can_be_supplied_at_approval(client, db):
    item = _dish(db)
    action = _promotion(db, item, {})
    ends = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()

    response = client.post(
        f"/api/v1/agents/actions/{action.id}/approve",
        json={"overrides": {"new_price": 9.0, "promo_ends_at": ends}},
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.promo_ends_at is not None


def test_a_promotion_cannot_end_in_the_past(client, db):
    item = _dish(db)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    action = _promotion(db, item, {"new_price": 8.0, "promo_ends_at": past})

    response = client.post(f"/api/v1/agents/actions/{action.id}/approve")

    assert response.status_code == 422
    assert "past" in response.json()["detail"]


def test_an_ended_promotion_restores_the_regular_price(db, changes):
    item = _dish(db, price=8.0)
    item.regular_price = 12.0
    item.promo_ends_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(item)
    db.commit()

    assert scheduler.expire_promotions(db) == 1

    db.refresh(item)
    assert item.price == 12.0
    assert item.regular_price is None
    assert item.promo_ends_at is None

    ended = changes(entity_type="menu_items", operation="update")[-1]
    assert ended.actor_type == "system"
    assert ended.note == "Promotion ended"
    assert ended.before["price"] == 8.0 and ended.after["price"] == 12.0


def test_a_running_promotion_is_left_alone(db):
    item = _dish(db, price=8.0)
    item.regular_price = 12.0
    item.promo_ends_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(item)
    db.commit()

    assert scheduler.expire_promotions(db) == 0
    db.refresh(item)
    assert item.price == 8.0


def test_editing_the_price_by_hand_ends_the_promotion(client, db):
    """Otherwise the expiry would later overwrite the new price with the old one."""
    item = _dish(db, price=8.0)
    item.regular_price = 12.0
    item.promo_ends_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(item)
    db.commit()

    client.patch(f"/api/v1/restaurant/menu-items/{item.id}", json={"price": 10.0})

    db.refresh(item)
    assert item.price == 10.0
    assert item.regular_price is None and item.promo_ends_at is None
    assert scheduler.expire_promotions(db) == 0


def test_stacking_promotions_keeps_the_original_regular_price(client, db):
    item = _dish(db, price=12.0)
    later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    first = _promotion(db, item, {"new_price": 9.0, "promo_ends_at": later})
    client.post(f"/api/v1/agents/actions/{first.id}/approve")

    second = AgentAction(
        run_id=first.run_id, agent_definition_id=first.agent_definition_id, action_type="menu_change",
        payload={"menu_item_id": item.id, "change_type": "promotion", "details": "deeper", "new_price": 7.0,
                 "promo_ends_at": later},
    )
    db.add(second)
    db.commit()
    client.post(f"/api/v1/agents/actions/{second.id}/approve")

    db.refresh(item)
    assert item.price == 7.0
    assert item.regular_price == 12.0  # not 9.0


def test_the_background_scheduler_registers_enabled_schedules():
    """The real BackgroundScheduler, against the app's own database."""
    from app.db.session import SessionLocal

    app_db = SessionLocal()
    defn = AgentDefinition(
        key="sched-probe", name="Probe", description="d", role="subagent", system_prompt="s",
        schedule_cron="0 9 * * *",
    )
    app_db.add(defn)
    app_db.commit()
    try:
        scheduler.start()
        jobs = {job.id for job in scheduler._scheduler.get_jobs()}
        assert "agent:sched-probe" in jobs
        assert scheduler.PROMOTION_SWEEP_JOB in jobs

        defn.enabled = False
        app_db.add(defn)
        app_db.commit()
        scheduler.sync_agent_schedules()
        assert "agent:sched-probe" not in {job.id for job in scheduler._scheduler.get_jobs()}
    finally:
        scheduler.shutdown()
        app_db.delete(defn)
        app_db.commit()
        app_db.close()
    assert not scheduler.is_running()


def test_runtime_reports_the_active_provider(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "ollama")
    monkeypatch.setattr("app.config.settings.ollama_model", "qwen3.5:4b")

    body = client.get("/api/v1/agents/runtime").json()

    assert body["provider"] == "ollama"
    assert body["model"] == "qwen3.5:4b"
    assert body["scheduler_running"] is False  # disabled under test
