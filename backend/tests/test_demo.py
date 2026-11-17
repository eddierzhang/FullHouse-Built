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
    # One agent runs everything, and every planted proposal is its.
    maestro = db.query(AgentDefinition).one()
    assert maestro.key == "maestro"
    assert {a.agent_definition_id for a in db.query(AgentAction)} == {maestro.id}
    assert (maestro.name, maestro.schedule_cron) == ("Assistant Manager", "0 9 * * *")

    # Six weeks of trading is enough for the weekday forecast.
    assert restaurant_crud.forecast(db)["method"] == "weekday"
    # Recipes drive food cost for every dish.
    assert {row["cost_source"] for row in restaurant_crud.menu_performance(db)} == {"recipe"}
    # Something runs out before a delivery could arrive.
    assert restaurant_crud.supply_chain_summary(db)["at_risk_count"] > 0
    # And a team member has no shifts.
    scheduled = {s.staff_id for s in db.query(Shift)}
    assert [p.name for p in db.query(Staff) if p.id not in scheduled] == ["Taylor Brooks"]


def test_the_proposal_queue_covers_every_kind_including_one_missing_its_price(db, monkeypatch):
    monkeypatch.setattr("app.config.settings.demo_mode", True)  # as the public demo runs: mail goes to the log
    reset_demo_data(db)

    pending = db.query(AgentAction).filter_by(status="pending").all()
    assert sorted(a.action_type for a in pending) == [
        "inventory_reorder", "menu_change", "purchase_order", "purchase_order", "purchase_order", "recipe", "shift_change",
    ]
    # Five dishes already have a confirmed recipe; the best seller without one gets a proposal.
    assert sorted(a.payload["name"] for a in pending if a.action_type == "recipe") == ["Tiramisu"]
    # One purchase order is the agent's own; the other comes from the invoices: romaine is at its reorder
    # point, and Sysco charged less for it than Green Valley last month. Tomatoes and chicken, which the
    # agent already proposed, are not proposed twice.
    from_prices = [a for a in pending if a.payload.get("source") == "prices"]
    assert [(a.payload["item_name"], a.payload["supplier_name"], a.payload["packs"]) for a in from_prices] == [
        ("Romaine Lettuce", "Sysco Foods", 7)
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
    assert db.query(InventoryItem).filter_by(name="Tomatoes").one().quantity_on_hand == 30
    assert db.query(InventoryItem).count() == 12  # nothing duplicated
    # Loading the demo is not itself history.
    assert db.query(ChangeLog).count() == 0


def test_scripted_maestro_looks_at_the_areas_a_task_mentions():
    areas = ["inventory", "suppliers", "staff", "menu", "profit"]

    assert route("How is stock looking, and any gaps in the staff schedule?", areas) == ["inventory", "staff"]
    assert route("Give me the morning briefing.", areas) == areas


def test_scripted_maestro_covers_several_areas_in_one_run(db, monkeypatch):
    reset_demo_data(db)
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: ScriptedProvider(step_delay=0))
    maestro = db.query(AgentDefinition).filter_by(key="maestro").one()
    run = agent_crud.create_run(db, maestro.id, input="Check stock and margins.")

    summary = execute_run(run.id, db=db)

    # One run does it all: no child runs, no delegation.
    assert db.query(AgentRun).filter(AgentRun.parent_run_id == run.id).count() == 0
    assert "inventory:" in summary and "profit:" in summary
    assert "{" not in summary  # read as sentences, not raw JSON
    events = agent_crud.list_events(db, run.id)
    assert "delegation" not in {e.type for e in events}
    assert [e.payload["tool"] for e in events if e.type == "tool_call"] == ["list_low_stock_items", "get_revenue_summary"]


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
            assert client.get("/api/v1/auth/options").status_code == 200
    finally:
        app.router.routes[:] = routes_before


def test_scripted_summaries_read_tool_results_as_sentences():
    assert describe('[{"id": "1", "name": "Tomatoes"}, {"id": "2", "name": "Basil"}]') == "2 found: Tomatoes, Basil"
    assert describe("[]") == "nothing found"
    assert describe('{"days": 7, "revenue": 4950.5, "orders": 150}') == "days 7, revenue 4950.5, orders 150"
    assert describe("plain text") == "plain text"


def test_every_page_of_the_demo_tells_the_same_story(db):
    """Sales on the operations pages are the sales the digest's prime cost rests on, and the figures are a real bistro's."""
    from datetime import date, timedelta

    from app.calendar.models import CalendarItem
    from app.documents.models import Document
    from app.forecast.models import PrepSheet
    from app.recipes.models import Recipe
    from app.sales import pnl
    from app.sales.models import PnlWeek, SalesDay

    reset_demo_data(db)

    week = db.query(PnlWeek).order_by(PnlWeek.week_of.desc()).first()
    assert db.query(PnlWeek).count() == 3
    orders = sum(o.total for o in db.query(Order) if week.week_of <= o.created_at.date() < week.week_of + timedelta(days=7))
    assert round(orders, 2) == round(week.sales_net, 2)
    for day in db.query(SalesDay):
        assert round(sum(o.total for o in db.query(Order) if o.created_at.date() == day.date), 2) == day.net_sales
    assert 18_000 < week.sales_net < 32_000
    assert 25 < week.food_pct < 38
    assert 28 < week.labor_pct < 34
    assert pnl.last_complete_week(date.today()) == week.week_of
    # Nothing a flag switches on opens empty.
    assert db.query(Recipe).filter_by(status="confirmed").count() == 5
    assert db.query(Document).count() >= 4
    assert db.query(CalendarItem).count() == 4
    assert db.query(PrepSheet).count() == 1
    assert db.query(InventoryItem).count() and sum(i.quantity_on_hand * i.unit_cost for i in db.query(InventoryItem)) > 3_000


def test_the_demo_shows_autopilot_passing_one_order_and_holding_another(db, monkeypatch):
    from app.ordering.models import AutopilotDecision, Outbox
    from app.restaurant.models import PurchaseOrder

    monkeypatch.setattr("app.config.settings.demo_mode", True)  # as the public demo runs: mail goes to the log
    reset_demo_data(db)

    decisions = {d.outcome: d for d in db.query(AutopilotDecision)}
    assert set(decisions) == {"passed", "held"}
    # Green Valley's order is inside its limits and waits out the cancel window, as a draft with its send queued.
    farm = db.query(PurchaseOrder).filter_by(autopilot=True).one()
    assert (farm.supplier_name, farm.status) == ("Green Valley Farms", "draft")
    assert db.query(Outbox).one().status == "pending"
    # Sysco's is over the owner's limit for one order: held, with the reason, for a person.
    assert [r["rule"] for r in decisions["held"].rules if not r["ok"]] == ["per_order_cap"]
    held = next(a for a in db.query(AgentAction).filter_by(status="pending") if (a.payload or {}).get("autopilot_held"))
    assert "over your $350.00 limit" in held.payload["autopilot_held"][0]


def test_the_demo_has_every_feature_on_except_voice(db):
    from app.platform import flags
    from app.restaurant.models import Restaurant

    reset_demo_data(db)

    on = flags.all_for(db, db.query(Restaurant).one().id)
    assert [flag for flag, enabled in on.items() if not enabled] == ["v3_voice"]
