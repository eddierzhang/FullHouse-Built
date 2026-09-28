"""When the AI is down, slow or misconfigured: the agent says so, and everything else works."""

import time

import pytest

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_DEF
from app.agents.models import AgentDefinition, AgentRun
from app.agents.providers.base import LLMProvider, ProviderError, ProviderResult
from app.agents.runner import execute_run
from test_restaurant_setup import _set_up

ITEMS = "/api/v1/restaurant/inventory-items"


def _agent(db) -> AgentDefinition:
    defn = db.query(AgentDefinition).filter_by(key=AGENT_DEF["key"]).first()
    if defn is None:
        defn = AgentDefinition(role="main", model="m", enabled=True, **AGENT_DEF)
        db.add(defn)
        db.commit()
    return defn


class Broken(LLMProvider):
    key = "broken"

    def resolve_model(self, configured_model):
        return "broken"

    def run_agent_loop(self, **kwargs):
        raise ProviderError("Gemini returned 503: the model is overloaded. Try again in a few minutes.")


class Stuck(LLMProvider):
    """Never finishes on its own; honours cancellation between steps, like the real providers."""

    key = "stuck"

    def resolve_model(self, configured_model):
        return "stuck"

    def run_agent_loop(self, *, should_cancel=None, **kwargs):
        from app.agents.providers.base import RunCancelledError

        while True:
            time.sleep(0.01)
            if should_cancel and should_cancel():
                raise RunCancelledError("stopped")


def test_an_ai_error_fails_the_run_with_the_reason(db, monkeypatch):
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: Broken())
    run = agent_crud.create_run(db, _agent(db).id, input="Check stock")

    with pytest.raises(ProviderError):
        execute_run(run.id, db=db)

    db.refresh(run)
    assert run.status == "failed"
    assert "overloaded" in run.error
    assert run.finished_at is not None


def test_a_stuck_ai_is_stopped_at_the_time_limit(db, monkeypatch):
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: Stuck())
    monkeypatch.setattr("app.config.settings.agent_run_timeout_seconds", 0.05)
    run = agent_crud.create_run(db, _agent(db).id, input="Check stock")

    started = time.monotonic()
    assert execute_run(run.id, db=db) == ""
    assert time.monotonic() - started < 5

    db.refresh(run)
    assert run.status == "failed"  # a time-out is a failure, not someone cancelling it
    assert run.error.startswith("Timed out")
    events = [e.payload for e in agent_crud.list_events(db, run.id) if e.type == "status_change"]
    assert events[-1]["status"] == "failed"


def test_a_cancel_is_still_a_cancel(db, monkeypatch):
    from app.agents import executor

    monkeypatch.setattr("app.agents.runner.get_provider", lambda: Stuck())
    run = agent_crud.create_run(db, _agent(db).id, input="Check stock")
    executor.request_cancel(run.id)
    try:
        execute_run(run.id, db=db)
    finally:
        executor.clear_cancel(run.id)
    db.refresh(run)
    assert run.status == "cancelled"


def test_manual_work_carries_on_while_the_ai_is_broken(client, db, monkeypatch):
    _set_up(client, db)

    def unavailable(*args, **kwargs):
        raise ProviderError("GEMINI_API_KEY is not set.")

    monkeypatch.setattr("app.agents.providers.get_provider", unavailable)
    monkeypatch.setattr("app.agents.runner.get_provider", unavailable)

    # Stock can be added and changed...
    item = client.post(ITEMS, json={"name": "Flour", "unit": "lb", "quantity_on_hand": 5, "reorder_threshold": 2})
    assert item.status_code == 201
    change = client.post(f"{ITEMS}/{item.json()['id']}/adjustments", json={"reason": "delivery", "quantity": 3})
    assert change.status_code == 200
    # ...the schedule and team can be read...
    assert client.get("/api/v1/restaurant/shifts").status_code == 200
    assert client.get("/api/v1/restaurant/staff").status_code == 200
    # ...and the status line says what's wrong instead of erroring.
    runtime = client.get("/api/v1/agents/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["provider"] == "unavailable"
    assert "GEMINI_API_KEY" in runtime.json()["ai_problem"]


def test_a_failed_run_leaves_no_half_made_changes(client, db, monkeypatch):
    """A run that breaks mid-way proposes nothing and changes no stock."""
    from app.agents.models import AgentAction

    _set_up(client, db)
    item = client.post(ITEMS, json={"name": "Basil", "unit": "bunch", "quantity_on_hand": 4, "reorder_threshold": 2})
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: Broken())
    run = agent_crud.create_run(db, _agent(db).id, input="Reorder basil")
    with pytest.raises(ProviderError):
        execute_run(run.id, db=db)

    assert db.query(AgentAction).count() == 0
    assert client.get(ITEMS).json()[0]["quantity_on_hand"] == item.json()["quantity_on_hand"]


def test_health_reports_an_unreachable_database(anon_client, monkeypatch):
    from app.db import session as app_session

    assert anon_client.get("/health").json() == {"status": "ok"}

    def down():
        raise OSError("connection refused")

    monkeypatch.setattr(app_session.engine, "connect", down)
    response = anon_client.get("/health")
    assert response.status_code == 503
    assert response.json()["detail"] == "database unreachable"


def test_runs_interrupted_by_a_restart_are_marked_failed(db):
    from app.main import _recover_orphaned_runs

    run = agent_crud.create_run(db, _agent(db).id, input="x")
    agent_crud.set_run_status(db, run, "running")
    assert _recover_orphaned_runs(db) == 1
    db.refresh(run)
    assert run.status == "failed" and "restarted" in run.error
    assert db.query(AgentRun).filter(AgentRun.status == "running").count() == 0
