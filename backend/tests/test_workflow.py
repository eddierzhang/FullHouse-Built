"""The whole daily loop, end to end through the API: stock runs low, the agent
notices and proposes, a manager approves, and every view reflects it."""

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_DEF
from app.agents.models import AgentAction, AgentDefinition
from app.agents.providers.scripted_provider import ScriptedProvider
from app.agents.runner import execute_run
from test_restaurant_setup import _set_up

API = "/api/v1/restaurant"
ITEMS = f"{API}/inventory-items"


def test_stock_change_to_agent_proposal_to_approval_to_dashboard(client, db, monkeypatch):
    restaurant = _set_up(client, db)
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: ScriptedProvider(step_delay=0))
    # The run is executed below, in this session; the background pool would run it a second time.
    monkeypatch.setattr("app.agents.executor.submit", lambda run_id, db=None: None)
    agent = db.query(AgentDefinition).filter_by(key=AGENT_DEF["key"]).one()

    # 1. Staff update inventory: saffron is used until it runs low.
    saffron = client.post(ITEMS, json={"name": "Saffron", "unit": "g", "quantity_on_hand": 10,
                                       "reorder_threshold": 5, "reorder_qty": 20}).json()
    used = client.post(f"{ITEMS}/{saffron['id']}/adjustments", json={"reason": "used", "quantity": 8},
                       headers={"Idempotency-Key": "used-saffron-1"})
    assert used.json()["item"]["quantity_on_hand"] == 2
    assert client.get(f"{API}/supply-chain").json()["low_stock_count"] == 1

    # 2. The agent reads the inventory and proposes a change.
    run = client.post("/api/v1/agents/runs", json={"agent_key": agent.key, "input": "Reorder the saffron, it's low"})
    assert run.status_code == 202, run.text
    # Run in this session rather than the thread pool, so the test sees its writes.
    execute_run(run.json()["id"], db=db)
    finished = client.get(f"/api/v1/agents/runs/{run.json()['id']}").json()
    assert finished["status"] == "succeeded"
    assert "saffron" in finished["output_summary"].lower()

    pending = client.get("/api/v1/agents/actions", params={"status": "pending"}).json()
    (proposal,) = [a for a in pending if a["payload"].get("item_name") == "Saffron"]
    assert proposal["action_type"] == "inventory_reorder"
    assert proposal["payload"]["quantity"] == 20

    # 3. A manager approves it -- once, however many times the button is pressed.
    approved = client.post(f"/api/v1/agents/actions/{proposal['id']}/approve", json={})
    assert approved.status_code == 200
    assert client.post(f"/api/v1/agents/actions/{proposal['id']}/approve", json={}).status_code == 400

    # 4. The order is placed, and the stock moves when it is received.
    assert {i["name"]: i for i in client.get(ITEMS).json()}["Saffron"]["quantity_on_hand"] == 2
    (order,) = client.get(f"{API}/purchase-orders").json()
    assert (order["item_name"], order["quantity"], order["status"], order["action_id"]) == ("Saffron", 20, "ordered", proposal["id"])
    received = client.post(f"{API}/purchase-orders/{order['id']}/receive", json={})
    assert received.status_code == 200, received.text
    assert client.get(f"{API}/purchase-orders").json() == []
    stock = {i["name"]: i for i in client.get(ITEMS).json()}
    assert stock["Saffron"]["quantity_on_hand"] == 22
    assert client.get(f"{API}/supply-chain").json()["low_stock_count"] == 0
    assert all(a["id"] != proposal["id"] for a in client.get("/api/v1/agents/actions", params={"status": "pending"}).json())
    history = client.get(f"{API}/inventory-adjustments", params={"inventory_item_id": saffron["id"]}).json()
    assert [h["reason"] for h in history] == ["reorder", "used", "initial"]

    # 5. And a mistake can be undone from the change history.
    change = client.get("/api/v1/changes", params={"entity_type": "inventory_items", "operation": "update"}).json()
    reorder_change = next(c for c in change["items"] if c.get("agent_action_id") == proposal["id"])
    assert client.post(f"/api/v1/changes/{reorder_change['id']}/revert").status_code == 200
    assert {i["name"]: i for i in client.get(ITEMS).json()}["Saffron"]["quantity_on_hand"] == 2
    assert db.query(AgentAction).filter_by(id=proposal["id"]).one().restaurant_id == restaurant["id"]


def test_the_scripted_agent_proposes_only_when_asked_to_reorder(client, db, monkeypatch):
    _set_up(client, db)
    monkeypatch.setattr("app.agents.runner.get_provider", lambda: ScriptedProvider(step_delay=0))
    agent = db.query(AgentDefinition).filter_by(key=AGENT_DEF["key"]).one()
    client.post(ITEMS, json={"name": "Basil", "unit": "bunch", "quantity_on_hand": 1, "reorder_threshold": 4})

    look = agent_crud.create_run(db, agent.id, input="How is stock looking?")
    execute_run(look.id, db=db)
    assert db.query(AgentAction).count() == 0

    act = agent_crud.create_run(db, agent.id, input="Please restock what's low")
    execute_run(act.id, db=db)
    (proposal,) = db.query(AgentAction).all()
    # No usual reorder quantity: enough to get back to twice the reorder point.
    assert proposal.payload["item_name"] == "Basil" and proposal.payload["quantity"] == 7
