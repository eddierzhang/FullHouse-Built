"""Several people at once, and the same request twice: nothing lost, nothing doubled."""

import threading

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_DEF
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.restaurant.models import InventoryAdjustment, InventoryItem, MenuItem, Order, RecipeItem, Supplier
from test_restaurant_setup import _set_up

API = "/api/v1/restaurant"
ITEMS = f"{API}/inventory-items"


def _item(client, **fields):
    body = {"name": "Tomatoes", "unit": "lb", "quantity_on_hand": 10, "reorder_threshold": 5, **fields}
    response = client.post(ITEMS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _delivery(client, item_id, quantity=5, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"{ITEMS}/{item_id}/adjustments", json={"reason": "delivery", "quantity": quantity},
                       headers=headers)


# --- the same request twice ------------------------------------------------------------


def test_a_delivery_sent_twice_is_recorded_once(client, db):
    _set_up(client, db)
    item = _item(client)

    first = _delivery(client, item["id"], key="delivery-123")
    again = _delivery(client, item["id"], key="delivery-123")

    assert first.status_code == again.status_code == 200
    assert again.json()["adjustment"]["id"] == first.json()["adjustment"]["id"]
    assert again.json()["item"]["quantity_on_hand"] == 15  # not 20
    assert db.query(InventoryAdjustment).filter_by(reason="delivery").count() == 1


def test_two_real_deliveries_both_count(client, db):
    _set_up(client, db)
    item = _item(client)
    _delivery(client, item["id"], key="a")
    assert _delivery(client, item["id"], key="b").json()["item"]["quantity_on_hand"] == 20
    # And without a key, every request is its own.
    assert _delivery(client, item["id"]).json()["item"]["quantity_on_hand"] == 25


def test_a_key_reused_on_another_item_is_refused(client, db):
    _set_up(client, db)
    tomatoes, basil = _item(client), _item(client, name="Basil", unit="bunch")
    _delivery(client, tomatoes["id"], key="k1")
    response = _delivery(client, basil["id"], key="k1")
    assert response.status_code == 409
    assert db.get(InventoryItem, basil["id"]).quantity_on_hand == 10


def test_an_unusable_key_is_refused(client, db):
    _set_up(client, db)
    item = _item(client)
    assert _delivery(client, item["id"], key="x" * 65).status_code == 422


def test_a_sale_sent_twice_is_rung_up_once(client, db):
    restaurant = _set_up(client, db)
    item = _item(client)
    dish = MenuItem(name="Salad", category="Salad", price=9, restaurant_id=restaurant["id"])
    db.add(dish)
    db.flush()
    db.add(RecipeItem(menu_item_id=dish.id, inventory_item_id=item["id"], quantity=1))
    db.commit()

    body = {"items": [{"menu_item_id": dish.id, "qty": 2}]}
    first = client.post(f"{API}/orders", json=body, headers={"Idempotency-Key": "sale-1"})
    again = client.post(f"{API}/orders", json=body, headers={"Idempotency-Key": "sale-1"})

    assert first.status_code == again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert db.query(Order).count() == 1
    db.expire_all()
    assert db.get(InventoryItem, item["id"]).quantity_on_hand == 8  # drawn down once


# --- two people editing the same thing ----------------------------------------------------


def test_an_edit_made_on_stale_values_is_refused(client, db):
    restaurant = _set_up(client, db)
    dish = MenuItem(name="Soup", category="Starter", price=6, restaurant_id=restaurant["id"])
    db.add(dish)
    db.commit()

    # Someone else changes the price first...
    assert client.patch(f"{API}/menu-items/{dish.id}", json={"price": 7}).status_code == 200
    # ...then this editor saves over the 6.00 their form still showed.
    stale = client.patch(f"{API}/menu-items/{dish.id}", json={"price": 6.5, "expected": {"price": 6}})
    assert stale.status_code == 409
    assert "price is now 7" in stale.json()["detail"]
    db.expire_all()
    assert db.get(MenuItem, dish.id).price == 7

    # Their edit to a field nobody else touched still goes through.
    ok = client.patch(f"{API}/menu-items/{dish.id}", json={"description": "Tomato", "expected": {"description": None}})
    assert ok.status_code == 200


def test_stale_edits_are_caught_on_items_and_suppliers_too(client, db):
    restaurant = _set_up(client, db)
    item = _item(client)
    supplier = Supplier(name="Farm", lead_time_days=2, restaurant_id=restaurant["id"])
    db.add(supplier)
    db.commit()

    assert client.patch(f"{ITEMS}/{item['id']}", json={"reorder_threshold": 8, "expected": {"reorder_threshold": 5}}).status_code == 200
    assert client.patch(f"{ITEMS}/{item['id']}", json={"reorder_threshold": 9, "expected": {"reorder_threshold": 5}}).status_code == 409
    assert client.patch(f"{API}/suppliers/{supplier.id}", json={"lead_time_days": 3, "expected": {"lead_time_days": 1}}).status_code == 409


# --- decisions and stock under real concurrency ---------------------------------------------


def _proposal(session) -> str:
    defn = session.query(AgentDefinition).filter_by(key=AGENT_DEF["key"]).first()
    if defn is None:
        defn = AgentDefinition(role="main", model="m", enabled=True, **AGENT_DEF)
        session.add(defn)
        session.flush()
    run = AgentRun(agent_definition_id=defn.id, status="succeeded", trigger_type="manual")
    session.add(run)
    session.flush()
    action = AgentAction(run_id=run.id, agent_definition_id=defn.id, action_type="inventory_reorder", payload={})
    session.add(action)
    session.commit()
    return action.id


def _race(threaded_db, attempt, workers=2):
    """Run `attempt(session)` in `workers` threads at once, each with its own session."""
    factory = sessionmaker(bind=threaded_db.get_bind(), autoflush=False, expire_on_commit=False)
    start = threading.Barrier(workers)
    results = []

    def worker():
        session = factory()
        try:
            start.wait()
            results.append(attempt(session))
        except OperationalError as exc:  # SQLite: "database is locked" -- the loser, refused outright
            session.rollback()
            assert "locked" in str(exc).lower()
            results.append(False)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def test_two_managers_approving_at_once_apply_it_once(threaded_db):
    action_id = _proposal(threaded_db)

    def approve(session):
        action = agent_crud.get_action(session, action_id)
        assert action.status == "pending"  # both saw it pending -- the race the check alone missed
        won = agent_crud.claim_pending(session, action, "approved")
        session.commit()
        return won

    assert sorted(_race(threaded_db, approve)) == [False, True]
    threaded_db.expire_all()
    assert threaded_db.get(AgentAction, action_id).status == "approved"


def test_a_decision_on_stale_status_is_refused(threaded_db):
    """The window the old check left open: read pending, then someone else decides."""
    action_id = _proposal(threaded_db)
    factory = sessionmaker(bind=threaded_db.get_bind(), autoflush=False, expire_on_commit=False)
    slow, fast = factory(), factory()

    stale = agent_crud.get_action(slow, action_id)
    slow.commit()  # end the read; `stale` still says pending
    other = agent_crud.get_action(fast, action_id)
    assert agent_crud.claim_pending(fast, other, "rejected")
    fast.commit()

    assert stale.status == "pending"
    assert not agent_crud.claim_pending(slow, stale, "approved")
    slow.rollback()
    for session in (slow, fast):
        session.close()


def test_simultaneous_sales_lose_no_stock_change(threaded_db):
    from app.restaurant import crud

    item = InventoryItem(name="Flour", unit="lb", quantity_on_hand=10, reorder_threshold=1)
    dish = MenuItem(name="Bread", category="Bakery", price=4)
    threaded_db.add_all([item, dish])
    threaded_db.flush()
    threaded_db.add(RecipeItem(menu_item_id=dish.id, inventory_item_id=item.id, quantity=1))
    threaded_db.commit()

    def sell(session):
        crud.check_and_deplete_stock(session, [(dish.id, 1)])
        session.commit()
        return True

    results = _race(threaded_db, sell, workers=4)
    threaded_db.expire_all()
    # Every sale that went through is reflected; none overwrote another.
    assert threaded_db.get(InventoryItem, item.id).quantity_on_hand == 10 - results.count(True)
    assert results.count(True) >= 1


def test_a_route_that_loses_the_claim_changes_nothing(client, db, monkeypatch):
    from app.agents.models import AgentAction as Action

    restaurant = _set_up(client, db)
    item = _item(client)
    action_id = _proposal(db)
    action = db.get(Action, action_id)
    action.restaurant_id = restaurant["id"]
    action.payload = {"inventory_item_id": item["id"], "quantity": 5}
    db.commit()

    monkeypatch.setattr("app.agents.crud.claim_pending", lambda *a, **k: False)
    response = client.post(f"/api/v1/agents/actions/{action_id}/approve", json={})

    assert response.status_code == 409
    assert "Someone else has just decided" in response.json()["detail"]
    db.expire_all()
    assert db.get(InventoryItem, item["id"]).quantity_on_hand == 10
    assert db.get(Action, action_id).status == "pending"


@pytest.mark.parametrize("path", ["approve", "reject"])
def test_deciding_twice_in_a_row_is_refused(client, db, path):
    restaurant = _set_up(client, db)
    action_id = _proposal(db)
    action = db.get(AgentAction, action_id)
    action.restaurant_id = restaurant["id"]
    action.action_type = "menu_change"
    action.payload = {"menu_item_id": "missing"}
    db.commit()
    first = client.post(f"/api/v1/agents/actions/{action_id}/reject")
    assert first.status_code == 200
    assert client.post(f"/api/v1/agents/actions/{action_id}/{path}", json={}).status_code == 400
