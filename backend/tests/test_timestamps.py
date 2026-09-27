"""Timestamps leave the API as UTC with an explicit offset, on every database."""

from datetime import datetime, timedelta, timezone

from app.agents import crud
from app.agents.models import AgentDefinition, AgentRun


def test_a_stored_timestamp_comes_back_utc_aware(db):
    defn = AgentDefinition(key="inventory", name="Inventory", description="d", role="subagent", system_prompt="s")
    db.add(defn)
    db.commit()
    moment = datetime(2026, 9, 13, 19, 46, 45, tzinfo=timezone.utc)
    run = crud.create_run(db, defn.id)
    run.started_at = moment.astimezone(timezone(timedelta(hours=-7)))  # written in another zone
    db.commit()
    db.expire_all()

    stored = db.get(AgentRun, run.id).started_at

    assert stored.tzinfo is not None
    assert stored == moment
    assert stored.utcoffset() == timedelta(0)


def test_the_api_sends_an_offset_a_browser_cannot_misread(client, db):
    """Without one, a browser west of UTC shows a run from a minute ago as hours in the future."""
    defn = AgentDefinition(key="inventory", name="Inventory", description="d", role="subagent", system_prompt="s")
    db.add(defn)
    db.commit()
    crud.create_run(db, defn.id)

    created_at = client.get("/api/v1/agents/runs").json()[0]["created_at"]

    assert created_at.endswith(("+00:00", "Z"))
