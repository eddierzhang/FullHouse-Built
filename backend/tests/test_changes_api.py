"""The read side: querying what happened."""

from app.audit.context import AGENT, Actor, actor_context
from app.restaurant.models import InventoryItem, MenuItem


def _seed(db):
    item = InventoryItem(name="Tomatoes", unit="lb", quantity_on_hand=10.0)
    db.add(item)
    db.commit()
    item.quantity_on_hand = 25.0
    db.commit()
    with actor_context(Actor(type=AGENT, id="inventory", agent_run_id="run-7")):
        item.quantity_on_hand = 5.0
        db.commit()
    return item


def test_list_returns_newest_first_with_a_total(client, db):
    _seed(db)

    body = client.get("/api/v1/changes").json()

    assert body["total"] == 3
    assert body["limit"] == 50 and body["offset"] == 0
    assert [c["operation"] for c in body["items"]] == ["update", "update", "insert"]


def test_filter_by_entity(client, db):
    item = _seed(db)
    db.add(MenuItem(name="Unrelated", category="x", price=1.0))
    db.commit()

    body = client.get(
        "/api/v1/changes", params={"entity_type": "inventory_items", "entity_id": item.id}
    ).json()

    assert body["total"] == 3
    assert {c["entity_type"] for c in body["items"]} == {"inventory_items"}


def test_filter_by_agent_run(client, db):
    _seed(db)

    body = client.get("/api/v1/changes", params={"agent_run_id": "run-7"}).json()

    assert body["total"] == 1
    assert body["items"][0]["after"] == {"quantity_on_hand": 5.0}
    assert body["items"][0]["actor_id"] == "inventory"


def test_filter_by_operation_and_actor_type(client, db):
    _seed(db)

    inserts = client.get("/api/v1/changes", params={"operation": "insert"}).json()
    agents = client.get("/api/v1/changes", params={"actor_type": "agent"}).json()

    assert inserts["total"] == 1
    assert agents["total"] == 1


def test_invalid_operation_filter_is_rejected(client, db):
    assert client.get("/api/v1/changes", params={"operation": "sideways"}).status_code == 422


def test_pagination_splits_the_result(client, db):
    _seed(db)

    first = client.get("/api/v1/changes", params={"limit": 2}).json()
    second = client.get("/api/v1/changes", params={"limit": 2, "offset": 2}).json()

    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert first["total"] == second["total"] == 3  # total ignores the page
    assert {c["id"] for c in first["items"]}.isdisjoint({c["id"] for c in second["items"]})


def test_entity_history_is_oldest_first(client, db):
    item = _seed(db)

    history = client.get(f"/api/v1/changes/entity/inventory_items/{item.id}").json()

    assert [c["operation"] for c in history] == ["insert", "update", "update"]
    assert history[1]["before"] == {"quantity_on_hand": 10.0}
    assert history[2]["after"] == {"quantity_on_hand": 5.0}


def test_get_one_change(client, db):
    _seed(db)
    listed = client.get("/api/v1/changes").json()["items"][0]

    fetched = client.get(f"/api/v1/changes/{listed['id']}").json()

    assert fetched == listed


def test_missing_change_is_404(client, db):
    assert client.get("/api/v1/changes/99999").status_code == 404
