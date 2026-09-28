"""Two restaurants on one site: neither can see or change the other's data,
whatever ids it puts in the URL or the request body.

The launch gate's "two-restaurant access test": restaurant A's owner
creates one of everything; restaurant B's owner then tries every route
against A's ids, and every list B loads must come back without A's rows.
"""

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentDefinition
from app.agents.runner import execute_run
from app.restaurant.models import Order
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/restaurant"


def _a_has_one_of_everything(client, db, restaurant_id):
    """Restaurant A's owner (`client`) creates a record of every kind."""
    supplier = client.post(f"{API}/suppliers", json={"name": "A Farms", "lead_time_days": 2}).json()
    item = client.post(f"{API}/inventory-items", json={
        "name": "A Flour", "unit": "kg", "quantity_on_hand": 50, "reorder_threshold": 5, "reorder_qty": 10,
        "unit_cost": 1, "supplier_id": supplier["id"],
    }).json()
    dish = client.post(f"{API}/menu-items", json={"name": "A Bread", "category": "Bakery", "price": 6, "cost": 1}).json()
    assert client.put(f"{API}/menu-items/{dish['id']}/recipe",
                      json={"lines": [{"inventory_item_id": item["id"], "quantity": 0.2}]}).status_code == 200
    order = client.post(f"{API}/orders", json={"items": [{"menu_item_id": dish["id"], "qty": 2}]}).json()
    person = client.post(f"{API}/staff", json={"name": "A Cook", "role": "Chef"}).json()
    shift = client.post(f"{API}/shifts", json={"staff_id": person["id"], "date": "2026-10-06",
                                               "start_time": "09:00", "end_time": "13:00", "role": "Chef"}).json()

    run = agent_crud.create_run(db, db.query(AgentDefinition).filter_by(key=AGENT_KEY).one().id,
                                input="check stock", restaurant_id=restaurant_id)
    action = agent_crud.create_action(db, run.id, run.agent_definition_id, "inventory_reorder",
                                      {"inventory_item_id": item["id"], "item_name": "A Flour", "quantity": 5})
    change = client.get("/api/v1/changes", params={"entity_type": "menu_items"}).json()["items"][0]
    return dict(supplier=supplier, item=item, dish=dish, order=order, person=person, shift=shift,
                run=run, action=action, change=change)


def test_another_restaurant_reads_every_id_as_missing(client, db):
    restaurant = _set_up(client, db)
    a = _a_has_one_of_everything(client, db, restaurant["id"])
    _, b = _second_restaurant(db)

    attempts = {
        "recipe": b.get(f"{API}/menu-items/{a['dish']['id']}/recipe"),
        "edit dish": b.patch(f"{API}/menu-items/{a['dish']['id']}", json={"price": 1}),
        "delete dish": b.delete(f"{API}/menu-items/{a['dish']['id']}"),
        "set recipe": b.put(f"{API}/menu-items/{a['dish']['id']}/recipe", json={"lines": []}),
        "edit supplier": b.patch(f"{API}/suppliers/{a['supplier']['id']}", json={"name": "Mine now"}),
        "delete supplier": b.delete(f"{API}/suppliers/{a['supplier']['id']}"),
        "edit item": b.patch(f"{API}/inventory-items/{a['item']['id']}", json={"name": "Mine now"}),
        "stock change": b.post(f"{API}/inventory-items/{a['item']['id']}/adjustments",
                               json={"reason": "waste", "quantity": 1}),
        "order A's dish": b.post(f"{API}/orders", json={"items": [{"menu_item_id": a['dish']['id'], "qty": 1}]}),
        "edit staff": b.patch(f"{API}/staff/{a['person']['id']}", json={"name": "Mine now"}),
        "delete shift": b.delete(f"{API}/shifts/{a['shift']['id']}"),
        "run": b.get(f"/api/v1/agents/runs/{a['run'].id}"),
        "stream": b.get(f"/api/v1/agents/runs/{a['run'].id}/stream"),
        "cancel": b.post(f"/api/v1/agents/runs/{a['run'].id}/cancel"),
        "approve": b.post(f"/api/v1/agents/actions/{a['action'].id}/approve"),
        "reject": b.post(f"/api/v1/agents/actions/{a['action'].id}/reject"),
        "change": b.get(f"/api/v1/changes/{a['change']['id']}"),
        "revert": b.post(f"/api/v1/changes/{a['change']['id']}/revert"),
    }
    assert {name: r.status_code for name, r in attempts.items() if r.status_code != 404} == {}

    # B may point its own records at A's too: refused the same way.
    b_item = b.post(f"{API}/inventory-items", json={
        "name": "B Salt", "unit": "kg", "quantity_on_hand": 1, "reorder_threshold": 1, "reorder_qty": 1, "unit_cost": 1,
    }).json()
    assert b.patch(f"{API}/inventory-items/{b_item['id']}", json={"supplier_id": a["supplier"]["id"]}).status_code == 404
    b_dish = b.post(f"{API}/menu-items", json={"name": "B Soup", "category": "Soup", "price": 5, "cost": 1}).json()
    assert b.put(f"{API}/menu-items/{b_dish['id']}/recipe",
                 json={"lines": [{"inventory_item_id": a['item']['id'], "quantity": 1}]}).status_code == 404

    # A's records are exactly as A left them.
    assert client.get(f"{API}/menu-items").json()[0]["price"] == 6
    assert [s["name"] for s in client.get(f"{API}/suppliers").json()] == ["A Farms"]
    assert client.get("/api/v1/agents/actions").json()[0]["status"] == "pending"


def test_another_restaurants_lists_and_summaries_leave_it_out(client, db):
    restaurant = _set_up(client, db)
    a = _a_has_one_of_everything(client, db, restaurant["id"])
    other, b = _second_restaurant(db)

    for path in ("/menu-items", "/menu-performance", "/suppliers", "/orders", "/inventory-items",
                 "/inventory-adjustments", "/staff", "/shifts"):
        assert b.get(f"{API}{path}").json() == [], path
    for path in ("/agents/runs", "/agents/actions"):
        assert b.get(f"/api/v1{path}").json() == [], path
    # B's history holds only the creation of B itself.
    assert [(c["entity_type"], c["entity_id"]) for c in b.get("/api/v1/changes").json()["items"]] == [
        ("restaurants", other.id)
    ]

    profit = b.get(f"{API}/profit-summary").json()
    assert (profit["revenue"], profit["orders"], profit["inventory_value"]) == (0, 0, 0)
    chain = b.get(f"{API}/supply-chain").json()
    assert (chain["supplier_count"], chain["item_count"]) == (0, 0)

    # A, meanwhile, sees its own.
    assert client.get(f"{API}/profit-summary").json()["orders"] == 1
    assert [o["id"] for o in client.get(f"{API}/orders").json()] == [a["order"]["id"]]
    assert db.get(Order, a["order"]["id"]).restaurant_id == restaurant["id"]


def test_an_agent_run_sees_only_its_own_restaurant(client, db, monkeypatch):
    restaurant = _set_up(client, db)
    _a_has_one_of_everything(client, db, restaurant["id"])
    other, b = _second_restaurant(db)
    b.post(f"{API}/inventory-items", json={
        "name": "B Salt", "unit": "kg", "quantity_on_hand": 0, "reorder_threshold": 1, "reorder_qty": 1, "unit_cost": 1,
    })
    monkeypatch.setattr("app.config.settings.llm_provider", "scripted")

    run = agent_crud.create_run(db, db.query(AgentDefinition).filter_by(key=AGENT_KEY).one().id,
                                input="check stock and suppliers", restaurant_id=other.id)
    execute_run(run.id, db=db)

    events = b.get(f"/api/v1/agents/runs/{run.id}").json()["events"]
    results = " ".join(str(e["payload"].get("result", "")) for e in events if e["type"] == "tool_result")
    assert "B Salt" in results
    assert "A Flour" not in results and "A Farms" not in results


# --- the migration, on a database that already has a restaurant's data --------------

def test_the_migration_gives_existing_data_to_the_sites_restaurant(tmp_path, monkeypatch):
    from alembic import command
    from sqlalchemy import create_engine, text

    from test_hr_staff import _alembic_config

    db_url = f"sqlite:///{tmp_path / 'populated.db'}"
    monkeypatch.setattr("app.config.settings.database_url", db_url)
    config = _alembic_config(db_url)
    command.upgrade(config, "b6e2f8a4c9d3")  # the revision before this one

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO restaurants (id, name, timezone, completed_steps, created_at, updated_at) "
            "VALUES ('r1', 'Casa', 'UTC', '[]', '2026-09-01 00:00:00', '2026-09-01 00:00:00')"
        ))
        conn.execute(text("INSERT INTO suppliers (id, name, lead_time_days) VALUES ('s1', 'Farm', 2)"))
        conn.execute(text("INSERT INTO menu_items (id, name, category, price, cost, is_available) "
                          "VALUES ('m1', 'Bread', 'Bakery', 5, 1, 1)"))
        conn.execute(text("INSERT INTO orders (id, status, channel, total, created_at) "
                          "VALUES ('o1', 'completed', 'dine_in', 10, '2026-09-02 12:00:00')"))
        conn.execute(text("INSERT INTO order_items (order_id, menu_item_id, qty, unit_price) VALUES ('o1', 'm1', 2, 5)"))
        conn.execute(text("INSERT INTO change_log (ts, entity_type, entity_id, operation, changed_fields, actor_type) "
                          "VALUES ('2026-09-02 12:00:00', 'menu_items', 'm1', 'insert', '[]', 'human')"))

    command.upgrade(config, "head")

    with engine.connect() as conn:
        for table in ("suppliers", "menu_items", "orders", "change_log"):
            assert conn.execute(text(f"SELECT restaurant_id FROM {table}")).scalar() == "r1", table
        assert conn.execute(text("SELECT qty FROM order_items")).scalar() == 2  # the order line survives the rebuild

    command.downgrade(config, "b6e2f8a4c9d3")
    with engine.connect() as conn:
        assert "restaurant_id" not in {c[1] for c in conn.execute(text("PRAGMA table_info(orders)"))}
        assert conn.execute(text("SELECT count(*) FROM order_items")).scalar() == 1
