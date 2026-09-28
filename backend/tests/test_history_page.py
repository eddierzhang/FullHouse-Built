"""What the History page reads: changes named in words, and a version to poll for live refresh."""

from app.restaurant.models import MenuItem, Staff
from test_restaurant_details import _member, _second_restaurant
from test_restaurant_setup import BASE, DETAILS, _set_up

ITEMS = "/api/v1/restaurant/inventory-items"
CHANGES = "/api/v1/changes"


def test_changes_are_named_in_words(client, db):
    restaurant = _set_up(client, db)
    item = client.post(ITEMS, json={"name": "Paprika", "unit": "g", "quantity_on_hand": 5, "reorder_threshold": 1}).json()
    client.patch(f"{ITEMS}/{item['id']}", json={"reorder_threshold": 2})
    person = Staff(name="Sam Patel", role="Server", restaurant_id=restaurant["id"])
    db.add(person)
    db.commit()
    shift = client.post("/api/v1/restaurant/shifts", json={
        "staff_id": person.id, "date": "2026-10-06", "start_time": "12:00", "end_time": "18:00", "role": "Server",
    }).json()
    client.patch(f"/api/v1/restaurant/shifts/{shift['id']}", json={"start_time": "13:00"})

    rows = client.get(CHANGES, params={"limit": 20}).json()["items"]
    labels = {(r["entity_type"], r["operation"]): r["entity_label"] for r in rows}

    # The edit's snapshot holds only the reorder point, yet it still reads as Paprika.
    assert labels[("inventory_items", "update")] == "Paprika"
    assert labels[("inventory_items", "insert")] == "Paprika"
    assert labels[("shifts", "update")] == "Sam Patel's shift on 2026-10-06"
    one = client.get(f"{CHANGES}/{rows[0]['id']}").json()
    assert one["entity_label"]


def test_a_deleted_record_keeps_its_name(client, db):
    restaurant = _set_up(client, db)
    dish = MenuItem(name="Old Soup", category="Starter", price=5, restaurant_id=restaurant["id"])
    db.add(dish)
    db.commit()
    assert client.delete(f"/api/v1/restaurant/menu-items/{dish.id}").status_code == 204

    (deleted,) = client.get(CHANGES, params={"entity_type": "menu_items", "operation": "delete"}).json()["items"]
    assert deleted["entity_label"] == "Old Soup"


def test_the_version_moves_when_something_changes(client, db):
    _set_up(client, db)
    before = client.get(f"{CHANGES}/version").json()
    assert before["version"] > 0  # setup itself is history

    unchanged = client.get(f"{CHANGES}/version").json()
    assert unchanged == before

    client.post(ITEMS, json={"name": "Salt", "unit": "kg", "quantity_on_hand": 1, "reorder_threshold": 1})
    after = client.get(f"{CHANGES}/version").json()
    assert after["version"] > before["version"]
    assert after["latest_at"] is not None


def test_another_restaurants_activity_never_moves_my_version(client, db):
    _set_up(client, db)
    mine = client.get(f"{CHANGES}/version").json()["version"]

    _, other = _second_restaurant(db)
    assert other.post(ITEMS, json={"name": "Their Salt", "unit": "kg", "quantity_on_hand": 1,
                                   "reorder_threshold": 1}).status_code == 201

    assert client.get(f"{CHANGES}/version").json()["version"] == mine


def test_members_version_ignores_profile_edits_they_cant_see(client, db):
    _set_up(client, db)
    member = _member(client, db)
    seen = member.get(f"{CHANGES}/version").json()["version"]

    assert client.put(f"{BASE}/details", json={**DETAILS, "name": "Casa Luna II"}).status_code == 200

    assert member.get(f"{CHANGES}/version").json()["version"] == seen
    assert client.get(f"{CHANGES}/version").json()["version"] > seen


def test_undo_stays_with_owners_and_admins(client, db):
    _set_up(client, db)
    item = client.post(ITEMS, json={"name": "Pepper", "unit": "g", "quantity_on_hand": 5, "reorder_threshold": 1}).json()
    client.patch(f"{ITEMS}/{item['id']}", json={"reorder_threshold": 3})
    change = client.get(CHANGES, params={"entity_type": "inventory_items", "operation": "update"}).json()["items"][0]

    member = _member(client, db)
    assert member.post(f"{CHANGES}/{change['id']}/revert").status_code == 403
    assert client.post(f"{CHANGES}/{change['id']}/revert").status_code == 200
