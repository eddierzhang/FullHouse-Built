"""Inventory: each restaurant's own stock, who may change what, and a history of every change."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.auth.models import ADMIN, User
from app.restaurant.models import InventoryAdjustment, InventoryItem, MenuItem, RecipeItem, Restaurant
from conftest import TEST_USER_EMAIL
from test_restaurant_details import _member, _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/restaurant"
ITEMS = f"{API}/inventory-items"
HISTORY = f"{API}/inventory-adjustments"


def _add(client, **fields):
    body = {"name": "Flour", "unit": "lb", "quantity_on_hand": 20, "reorder_threshold": 10, **fields}
    response = client.post(ITEMS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _change(client, item_id, reason, quantity, note=None):
    return client.post(f"{ITEMS}/{item_id}/adjustments", json={"reason": reason, "quantity": quantity, "note": note})


def _history(client, item_id=None):
    return client.get(HISTORY, params={"inventory_item_id": item_id} if item_id else {}).json()


# --- two accounts, two restaurants ---------------------------------------------------


def test_coworkers_share_stock_and_another_restaurant_stays_separate(client, db):
    restaurant = _set_up(client, db)
    member = _member(client, db)  # a coworker at the same restaurant
    other, other_owner = _second_restaurant(db)

    flour = _add(client, name="Flour", quantity_on_hand=20)
    _add(other_owner, name="Flour", quantity_on_hand=99)  # the same name is fine elsewhere

    # The coworker sees the owner's item, and records a delivery on it...
    assert [i["name"] for i in member.get(ITEMS).json()] == ["Flour"]
    response = _change(member, flour["id"], "delivery", 5, note="Tuesday drop")
    assert response.status_code == 200
    assert response.json()["item"]["quantity_on_hand"] == 25

    # ...which the owner sees straight away, with who did it.
    (mine,) = client.get(ITEMS).json()
    assert mine["quantity_on_hand"] == 25
    latest = _history(client, flour["id"])[0]
    assert (latest["reason"], latest["change"], latest["actor_name"], latest["note"]) == (
        "delivery", 5, "Mo", "Tuesday drop"
    )

    # The other restaurant's stock is untouched and invisible from here.
    (theirs,) = other_owner.get(ITEMS).json()
    assert theirs["quantity_on_hand"] == 99 and theirs["id"] != flour["id"]
    assert all(h["inventory_item_id"] != flour["id"] for h in _history(other_owner))
    assert all(h["inventory_item_id"] != theirs["id"] for h in _history(client))

    db.expire_all()
    assert db.get(InventoryItem, flour["id"]).restaurant_id == restaurant["id"]
    assert db.get(InventoryItem, theirs["id"]).restaurant_id == other.id


def test_another_restaurants_items_read_as_missing(client, db):
    _set_up(client, db)
    flour = _add(client)
    _, other_owner = _second_restaurant(db)

    assert _change(other_owner, flour["id"], "used", 1).status_code == 404
    assert other_owner.patch(f"{ITEMS}/{flour['id']}", json={"name": "Stolen"}).status_code == 404
    assert other_owner.get(HISTORY, params={"inventory_item_id": flour["id"]}).status_code == 404
    assert client.get(ITEMS).json()[0]["quantity_on_hand"] == 20

    # Nor can their dishes be made from it.
    dish = MenuItem(name="Bread", category="Bakery", price=5)
    db.add(dish)
    db.commit()
    response = other_owner.put(
        f"{API}/menu-items/{dish.id}/recipe", json={"lines": [{"inventory_item_id": flour["id"], "quantity": 1}]}
    )
    assert response.status_code == 404


def test_summaries_count_only_this_restaurants_stock(client, db):
    _set_up(client, db)
    _add(client, name="Flour", quantity_on_hand=2, reorder_threshold=10, unit_cost=1)
    _, other_owner = _second_restaurant(db)
    _add(other_owner, name="Rice", quantity_on_hand=100, unit_cost=3)

    mine = client.get(f"{API}/supply-chain").json()
    theirs = other_owner.get(f"{API}/supply-chain").json()
    assert (mine["item_count"], mine["low_stock_count"], mine["total_stock_value"]) == (1, 1, 2)
    assert (theirs["item_count"], theirs["low_stock_count"], theirs["total_stock_value"]) == (1, 0, 300)
    assert other_owner.get(f"{API}/profit-summary").json()["inventory_value"] == 300


def test_the_agent_works_with_the_sites_own_stock(client, db):
    from app.restaurant import crud

    _set_up(client, db)
    flour = _add(client)
    _, other_owner = _second_restaurant(db)
    rice = _add(other_owner, name="Rice")

    assert [i.id for i in crud.list_inventory_items(db)] == [flour["id"]]
    assert crud.get_inventory_item(db, rice["id"]) is None


# --- adding items ------------------------------------------------------------------


def test_adding_an_item_records_its_opening_stock(client, db):
    _set_up(client, db)
    item = _add(client, name="  Basil ", unit="BUNCH", quantity_on_hand=6, reorder_threshold=8)

    assert (item["name"], item["unit"], item["low_stock"]) == ("Basil", "bunch", True)
    (opening,) = _history(client, item["id"])
    assert (opening["reason"], opening["change"], opening["quantity_after"]) == ("initial", 6, 6)
    assert opening["actor_id"] == TEST_USER_EMAIL


def test_members_can_add_items(client, db):
    _set_up(client, db)
    member = _member(client, db)
    assert member.post(ITEMS, json={"name": "Salt", "unit": "kg"}).status_code == 201


@pytest.mark.parametrize(
    "body,status",
    [
        ({"name": "Flour", "unit": "parsecs"}, 422),
        ({"name": "Flour", "unit": ""}, 422),
        ({"name": "   ", "unit": "lb"}, 422),
        ({"name": "Flour", "unit": "lb", "quantity_on_hand": -1}, 422),
        ({"name": "Flour", "unit": "lb", "reorder_threshold": -3}, 422),
        ({"name": "Flour", "unit": "lb", "quantity_on_hand": 10_000_001}, 422),
        ({"name": "flour", "unit": "lb"}, 409),  # already tracked, whatever the case
    ],
)
def test_bad_new_items_are_refused(client, db, body, status):
    _set_up(client, db)
    _add(client)
    assert client.post(ITEMS, json=body).status_code == status
    assert len(client.get(ITEMS).json()) == 1


# --- recording stock changes ---------------------------------------------------------


def test_each_kind_of_change_moves_stock_the_right_way(client, db):
    _set_up(client, db)
    item = _add(client, quantity_on_hand=20)

    assert _change(client, item["id"], "delivery", 10).json()["item"]["quantity_on_hand"] == 30
    assert _change(client, item["id"], "used", 4.5).json()["item"]["quantity_on_hand"] == 25.5
    assert _change(client, item["id"], "waste", 0.5).json()["item"]["quantity_on_hand"] == 25
    counted = _change(client, item["id"], "count", 23).json()
    assert counted["item"]["quantity_on_hand"] == 23
    assert counted["adjustment"]["change"] == -2

    history = _history(client, item["id"])
    assert [(h["reason"], h["change"], h["quantity_after"]) for h in history] == [
        ("count", -2, 23), ("waste", -0.5, 25), ("used", -4.5, 25.5), ("delivery", 10, 30), ("initial", 20, 20)
    ]
    # The history adds up to what's on hand.
    assert sum(h["change"] for h in history) == client.get(ITEMS).json()[0]["quantity_on_hand"]


def test_stock_cannot_go_negative(client, db):
    _set_up(client, db)
    item = _add(client, quantity_on_hand=3)

    response = _change(client, item["id"], "used", 5)
    assert response.status_code == 422
    assert "Only 3 lb of Flour is on hand" in response.json()["detail"]
    assert _change(client, item["id"], "waste", 3.01).status_code == 422
    assert _change(client, item["id"], "count", -1).status_code == 422
    assert client.get(ITEMS).json()[0]["quantity_on_hand"] == 3
    assert len(_history(client, item["id"])) == 1  # just the opening stock

    # Using exactly what's there is fine.
    assert _change(client, item["id"], "used", 3).json()["item"]["quantity_on_hand"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"reason": "used", "quantity": 0},
        {"reason": "delivery", "quantity": -2},
        {"reason": "theft", "quantity": 1},
        {"reason": "sale", "quantity": 1},  # sales come from orders, not by hand
        {"reason": "delivery"},
        {"reason": "delivery", "quantity": 1, "note": "x" * 256},
    ],
)
def test_bad_stock_changes_are_refused(client, db, body):
    _set_up(client, db)
    item = _add(client)
    assert client.post(f"{ITEMS}/{item['id']}/adjustments", json=body).status_code == 422
    assert len(_history(client, item["id"])) == 1


def test_a_count_that_matches_changes_nothing(client, db):
    _set_up(client, db)
    item = _add(client, quantity_on_hand=20)
    response = _change(client, item["id"], "count", 20)
    assert response.status_code == 200
    assert response.json()["adjustment"] is None
    assert len(_history(client, item["id"])) == 1


def test_a_change_moves_updated_at(client, db):
    _set_up(client, db)
    item = _add(client)
    changed = _change(client, item["id"], "delivery", 1).json()["item"]
    assert changed["updated_at"] > item["updated_at"]


# --- editing details ------------------------------------------------------------------


def test_only_owners_and_admins_edit_details(client, db):
    _set_up(client, db)
    item = _add(client)
    member = _member(client, db)

    response = member.patch(f"{ITEMS}/{item['id']}", json={"reorder_threshold": 50})
    assert response.status_code == 403
    assert "record stock changes" in response.json()["detail"]
    assert _change(member, item["id"], "used", 1).status_code == 200  # but they can record stock

    mo = db.query(User).filter_by(email="mo@example.com").one()
    mo.role = ADMIN
    db.commit()
    edited = member.patch(f"{ITEMS}/{item['id']}", json={"reorder_threshold": 50, "unit": "KG", "name": "Bread flour"})
    assert edited.status_code == 200
    assert (edited.json()["reorder_threshold"], edited.json()["unit"], edited.json()["name"]) == (50, "kg", "Bread flour")


@pytest.mark.parametrize(
    "body,status",
    [
        ({"quantity_on_hand": 5}, 422),  # stock only changes with a recorded reason
        ({"unit": "parsecs"}, 422),
        ({"name": ""}, 422),
        ({"name": None}, 422),
        ({"reorder_qty": -1}, 422),
        ({"name": "Sugar"}, 409),
        ({"supplier_id": "ghost"}, 404),
    ],
)
def test_bad_detail_edits_are_refused(client, db, body, status):
    _set_up(client, db)
    item = _add(client)
    _add(client, name="Sugar")
    assert client.patch(f"{ITEMS}/{item['id']}", json=body).status_code == status
    assert len(_history(client, item["id"])) == 1


# --- every other way stock changes is recorded too ---------------------------------------


def test_sales_approvals_and_reverts_are_in_the_history(client, db):
    from app.agents.appliers import apply_action
    from app.agents.models import AgentAction

    restaurant = _set_up(client, db)
    item = _add(client, quantity_on_hand=10)

    # A sale draws the recipe's ingredients down.
    dish = MenuItem(name="Bread", category="Bakery", price=5, restaurant_id=restaurant["id"])
    db.add(dish)
    db.flush()
    db.add(RecipeItem(menu_item_id=dish.id, inventory_item_id=item["id"], quantity=0.5))
    db.commit()
    assert client.post(f"{API}/orders", json={"items": [{"menu_item_id": dish.id, "qty": 4}]}).status_code == 200

    # An approved reorder books the delivery.
    action = AgentAction(run_id="r", agent_definition_id="a", action_type="inventory_reorder",
                         payload={"inventory_item_id": item["id"], "quantity": 5})
    apply_action(db, action)
    db.commit()

    # And undoing that from the change history takes it back out.
    change = client.get("/api/v1/changes", params={"entity_type": "inventory_items", "operation": "update"}).json()
    assert client.post(f"/api/v1/changes/{change['items'][0]['id']}/revert").status_code == 200

    history = _history(client, item["id"])
    assert [(h["reason"], h["change"]) for h in history] == [("revert", -5), ("reorder", 5), ("sale", -2), ("initial", 10)]
    assert history[0]["quantity_after"] == 8
    assert client.get(ITEMS).json()[0]["quantity_on_hand"] == 8


def test_stock_seeded_before_setup_is_claimed_by_the_restaurant(client, db):
    db.add(InventoryItem(name="Sample Flour", unit="lb", quantity_on_hand=5))
    db.commit()
    # Before setup, the owner-to-be sees the unclaimed sample stock...
    assert [i["name"] for i in client.get(ITEMS).json()] == ["Sample Flour"]

    restaurant = _set_up(client, db)
    db.expire_all()
    item = db.query(InventoryItem).one()
    assert item.restaurant_id == restaurant["id"]
    assert db.query(InventoryAdjustment).filter_by(inventory_item_id=item.id).one().restaurant_id == restaurant["id"]

    # ...and another restaurant never does.
    _, other_owner = _second_restaurant(db)
    assert other_owner.get(ITEMS).json() == []


# --- the migration, on a database that already has stock ------------------------------------

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


@pytest.mark.parametrize("with_restaurant", [True, False])
def test_the_migration_keeps_existing_stock_and_starts_its_history(tmp_path, monkeypatch, with_restaurant):
    db_url = f"sqlite:///{tmp_path / 'populated.db'}"
    monkeypatch.setattr("app.config.settings.database_url", db_url)
    config = _alembic_config(db_url)
    command.upgrade(config, "d4f7b1c9e2a6")  # the revision before inventory was per restaurant

    engine = create_engine(db_url)
    with engine.begin() as conn:
        if with_restaurant:
            conn.execute(text(
                "INSERT INTO restaurants (id, name, timezone, completed_steps, created_at, updated_at) "
                "VALUES ('r1', 'Casa', 'UTC', '[]', '2026-09-01 00:00:00', '2026-09-01 00:00:00')"
            ))
        conn.execute(text(
            "INSERT INTO inventory_items (id, name, unit, quantity_on_hand, reorder_threshold, reorder_qty, unit_cost) "
            "VALUES ('i1', 'Flour', 'lb', 12.5, 5, 10, 1), ('i2', 'Salt', 'kg', 0, 1, 2, 1)"
        ))

    command.upgrade(config, "head")

    with engine.connect() as conn:
        items = conn.execute(text("SELECT id, restaurant_id, quantity_on_hand, updated_at FROM inventory_items ORDER BY id")).all()
        history = conn.execute(text(
            "SELECT inventory_item_id, restaurant_id, change, quantity_after, reason FROM inventory_adjustments"
        )).all()
    expected_restaurant = "r1" if with_restaurant else None
    assert [(i.id, i.restaurant_id, i.quantity_on_hand) for i in items] == [
        ("i1", expected_restaurant, 12.5), ("i2", expected_restaurant, 0)
    ]
    assert all(i.updated_at is not None for i in items)
    # One opening entry per item holding stock, so each history adds up.
    assert [tuple(h) for h in history] == [("i1", expected_restaurant, 12.5, 12.5, "initial")]

    command.downgrade(config, "d4f7b1c9e2a6")
    with engine.connect() as conn:
        columns = {c[1] for c in conn.execute(text("PRAGMA table_info(inventory_items)"))}
        assert conn.execute(text("SELECT quantity_on_hand FROM inventory_items WHERE id = 'i1'")).scalar() == 12.5
    assert "restaurant_id" not in columns and "updated_at" not in columns
