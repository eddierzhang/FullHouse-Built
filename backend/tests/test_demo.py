"""The public demo: its data, its reset, and what visitors can do with it."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.agents import crud as agent_crud
from app.agents.appliers import apply_action, missing_inputs
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.agents.providers.scripted_provider import ScriptedProvider, describe, route
from app.agents.runner import execute_run
from app.audit.models import ChangeLog
from app.demo import reset_demo_data
from app.restaurant import crud as restaurant_crud
from app.restaurant.models import InventoryItem, Order, Shift, Staff


def test_the_demo_gives_every_page_something_real_to_show(db):
    counts = reset_demo_data(db)

    assert counts["orders"] > 500
    assert db.query(AgentDefinition).count() == 6
    maestro = db.query(AgentDefinition).filter_by(key="boss").one()
    assert (maestro.name, maestro.schedule_cron) == ("Maestro", "0 9 * * *")

    # Six weeks of trading is enough for the weekday forecast.
    assert restaurant_crud.forecast(db)["method"] == "weekday"
    # Recipes drive food cost for every dish.
    assert {row["cost_source"] for row in restaurant_crud.menu_performance(db)} == {"recipe"}
    # Something runs out before a delivery could arrive.
    assert restaurant_crud.supply_chain_summary(db)["at_risk_count"] > 0
    # And a team member has no shifts.
    scheduled = {s.staff_id for s in db.query(Shift)}
    assert [p.name for p in db.query(Staff) if p.id not in scheduled] == ["Taylor Brooks"]


def test_the_proposal_queue_covers_every_kind_including_one_missing_its_price(db):
    reset_demo_data(db)

    pending = db.query(AgentAction).filter_by(status="pending").all()
    assert sorted(a.action_type for a in pending) == [
        "inventory_reorder", "menu_change", "purchase_order", "shift_change",
    ]
    promotion = next(a for a in pending if a.action_type == "menu_change")
    assert missing_inputs(promotion) == ["new_price"]

    # Every other proposal applies cleanly against the demo data.
    for action in pending:
        if action is promotion:
            continue
        assert apply_action(db, action)
        db.rollback()

    # Each came from a finished run whose log can be replayed.
    for action in pending:
        assert [e.type for e in agent_crud.list_events(db, action.run_id)][0] == "status_change"


def test_a_reset_undoes_whatever_visitors_did(db):
    reset_demo_data(db)
    tomatoes = db.query(InventoryItem).filter_by(name="Tomatoes").one()
    tomatoes.quantity_on_hand = 999
    db.add(tomatoes)
    db.delete(db.query(Order).first())
    db.commit()
    assert db.query(ChangeLog).count() > 0

    before = reset_demo_data(db)
    after = reset_demo_data(db)

    assert before == after  # the same restaurant every time
    assert db.query(InventoryItem).filter_by(name="Tomatoes").one().quantity_on_hand == 6
    assert db.query(InventoryItem).count() == 12  # nothing duplicated
    # Loading the demo is not itself history.
    assert db.query(ChangeLog).count() == 0


def test_scripted_maestro_routes_a_task_to_the_specialists_it_mentions():
    agents = ["inventory", "supply_chain", "employee_management", "profit", "marketing"]

    assert route("How is stock looking, and any gaps in the staff schedule?", agents) == [
        "inventory", "employee_management",
    ]
    assert route("Give me the morning briefing.", agents) == agents


def test_scripted_maestro_delegates_in_parallel_against_the_demo(threaded_db, monkeypatch):
    reset_demo_data(threaded_db)
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: ScriptedProvider(step_delay=0))
    maestro = threaded_db.query(AgentDefinition).filter_by(key="boss").one()
    run = agent_crud.create_run(threaded_db, maestro.id, input="Check stock and margins.")

    summary = execute_run(run.id, db=threaded_db)

    threaded_db.expire_all()
    children = threaded_db.query(AgentRun).filter(AgentRun.parent_run_id == run.id).all()
    assert sorted(c.status for c in children) == ["succeeded", "succeeded"]
    assert "inventory:" in summary and "profit:" in summary
    assert "{" not in summary  # read as sentences, not raw JSON
    kinds = [e.type for e in agent_crud.list_events(threaded_db, run.id)]
    assert kinds.count("delegation") == 2


def test_runtime_reports_demo_mode(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.demo_mode", True)

    body = client.get("/api/v1/agents/runtime").json()

    assert body["demo_mode"] is True
    assert body["demo_resets_at"]


def test_one_process_can_serve_the_frontend_and_the_api(tmp_path: Path, monkeypatch):
    (tmp_path / "index.html").write_text("<div id=\"root\"></div>", encoding="utf-8")
    monkeypatch.setattr("app.config.settings.scheduler_enabled", False)

    from app.main import app, mount_frontend

    routes_before = list(app.router.routes)
    try:
        mount_frontend(app, str(tmp_path))
        with TestClient(app) as client:
            assert 'id="root"' in client.get("/").text
            assert client.get("/health").json() == {"status": "ok"}  # API routes still win
            assert client.get("/api/v1/agents/runtime").status_code == 200
    finally:
        app.router.routes[:] = routes_before


def test_scripted_summaries_read_tool_results_as_sentences():
    assert describe('[{"id": "1", "name": "Tomatoes"}, {"id": "2", "name": "Basil"}]') == "2 found: Tomatoes, Basil"
    assert describe("[]") == "nothing found"
    assert describe('{"days": 7, "revenue": 4950.5, "orders": 150}') == "days 7, revenue 4950.5, orders 150"
    assert describe("plain text") == "plain text"
