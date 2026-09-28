"""Units that stay consistent -- packs vs the unit stock is counted in -- and importing a spreadsheet."""

import pytest

from app.restaurant.models import InventoryAdjustment, InventoryItem, MenuItem, RecipeItem, Supplier
from test_restaurant_details import _member, _second_restaurant
from test_restaurant_setup import _set_up

ITEMS = "/api/v1/restaurant/inventory-items"
IMPORT = f"{ITEMS}/import"


def _eggs(client, **fields):
    body = {"name": "Eggs", "unit": "each", "quantity_on_hand": 30, "reorder_threshold": 24,
            "pack_unit": "Case", "pack_size": 180, **fields}
    response = client.post(ITEMS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- packs --------------------------------------------------------------------------


def test_an_item_can_be_bought_in_packs_and_counted_in_units(client, db):
    _set_up(client, db)
    eggs = _eggs(client)
    assert (eggs["unit"], eggs["pack_unit"], eggs["pack_size"]) == ("each", "case", 180)

    # Two cases arrive: stock goes up by 360 eggs, and the history says how it was entered.
    change = client.post(f"{ITEMS}/{eggs['id']}/adjustments", json={"reason": "delivery", "quantity": 2, "in_packs": True})
    assert change.status_code == 200
    assert change.json()["item"]["quantity_on_hand"] == 390
    assert change.json()["adjustment"]["change"] == 360
    assert change.json()["adjustment"]["note"] == "2 cases of 180 each"

    # A count in cases sets the level in eggs.
    count = client.post(f"{ITEMS}/{eggs['id']}/adjustments",
                        json={"reason": "count", "quantity": 1.5, "in_packs": True, "note": "walk-in"})
    assert count.json()["item"]["quantity_on_hand"] == 270
    assert count.json()["adjustment"]["note"] == "1.5 cases of 180 each. walk-in"


def test_packs_only_where_an_item_has_them(client, db):
    _set_up(client, db)
    flour = client.post(ITEMS, json={"name": "Flour", "unit": "lb", "quantity_on_hand": 5, "reorder_threshold": 1}).json()
    response = client.post(f"{ITEMS}/{flour['id']}/adjustments", json={"reason": "delivery", "quantity": 1, "in_packs": True})
    assert response.status_code == 422
    assert "isn't bought in packs" in response.json()["detail"]


@pytest.mark.parametrize(
    "fields",
    [
        {"pack_unit": "case"},  # no size
        {"pack_size": 12},  # no pack
        {"pack_unit": "each", "pack_size": 12},  # the pack is the unit itself
        {"pack_unit": "crate", "pack_size": 12},  # not a unit we track
        {"pack_unit": "case", "pack_size": 0},
    ],
)
def test_bad_packs_are_refused(client, db, fields):
    _set_up(client, db)
    body = {"name": "Limes", "unit": "each", "quantity_on_hand": 0, "reorder_threshold": 0, **fields}
    assert client.post(ITEMS, json=body).status_code == 422


def test_packs_can_be_changed_and_cleared(client, db):
    _set_up(client, db)
    eggs = _eggs(client)
    assert client.patch(f"{ITEMS}/{eggs['id']}", json={"pack_size": 360}).json()["pack_size"] == 360
    cleared = client.patch(f"{ITEMS}/{eggs['id']}", json={"pack_unit": None}).json()
    assert (cleared["pack_unit"], cleared["pack_size"]) == (None, None)
    assert client.patch(f"{ITEMS}/{eggs['id']}", json={"pack_size": 12}).status_code == 422  # a size needs a pack


# --- the unit itself ----------------------------------------------------------------


def test_the_unit_cant_silently_relabel_stock(client, db):
    _set_up(client, db)
    flour = client.post(ITEMS, json={"name": "Flour", "unit": "lb", "quantity_on_hand": 12, "reorder_threshold": 1}).json()

    refused = client.patch(f"{ITEMS}/{flour['id']}", json={"unit": "kg"})
    assert refused.status_code == 409
    assert "would relabel that as 12 kg" in refused.json()["detail"]

    # Counted out to zero, the unit can change; then it's counted again in the new unit.
    client.post(f"{ITEMS}/{flour['id']}/adjustments", json={"reason": "count", "quantity": 0})
    assert client.patch(f"{ITEMS}/{flour['id']}", json={"unit": "kg"}).json()["unit"] == "kg"


def test_a_unit_used_in_recipes_stays(client, db):
    restaurant = _set_up(client, db)
    salt = client.post(ITEMS, json={"name": "Salt", "unit": "g", "quantity_on_hand": 0, "reorder_threshold": 0}).json()
    dish = MenuItem(name="Chips", category="Side", price=4, restaurant_id=restaurant["id"])
    db.add(dish)
    db.flush()
    db.add(RecipeItem(menu_item_id=dish.id, inventory_item_id=salt["id"], quantity=2))
    db.commit()
    response = client.patch(f"{ITEMS}/{salt['id']}", json={"unit": "kg"})
    assert response.status_code == 409 and "Recipes use Salt in g" in response.json()["detail"]


# --- importing a spreadsheet -----------------------------------------------------------

SHEET = """Item,Qty,UOM,Par,Order Qty,Cost,Vendor,Case,Units per case
Tomatoes,"1,200",lbs,300,500,$0.89,Green Valley,,
Eggs,90,ea,60,180,0.22,Harbor Dairy,cases,180
Olive Oil,4,Liters,2,6,11.50,Sysco,,
"""


def test_a_spreadsheet_is_previewed_then_imported(client, db):
    _set_up(client, db)
    db.add(Supplier(name="Sysco", lead_time_days=2, restaurant_id=client.get("/api/v1/auth/me").json()["restaurant_id"]))
    db.commit()

    preview = client.post(IMPORT, json={"csv": SHEET})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["new"], body["errors"], body["saved"]) == (3, 0, False)
    assert body["new_suppliers"] == ["Green Valley", "Harbor Dairy"]  # Sysco already exists
    assert [(r["name"], r["unit"], r["quantity_on_hand"]) for r in body["rows"]] == [
        ("Tomatoes", "lb", 1200), ("Eggs", "each", 90), ("Olive Oil", "L", 4),
    ]
    assert db.query(InventoryItem).count() == 0  # a preview saves nothing

    done = client.post(IMPORT, json={"csv": SHEET, "dry_run": False}).json()
    assert done["saved"] is True
    items = {i["name"]: i for i in client.get(ITEMS).json()}
    assert items["Eggs"]["pack_unit"] == "case" and items["Eggs"]["pack_size"] == 180
    assert items["Tomatoes"]["reorder_threshold"] == 300 and items["Tomatoes"]["unit_cost"] == 0.89
    # Opening stock is in the history, like any other item's.
    assert db.query(InventoryAdjustment).filter_by(reason="initial").count() == 3
    assert db.query(Supplier).count() == 3


def test_one_bad_row_imports_nothing_and_says_where(client, db):
    _set_up(client, db)
    sheet = "Name,Unit,On hand\nFlour,lb,10\nSugar,parsecs,5\nSalt,g,lots\n,g,1\nFlour,lb,2\n"
    body = client.post(IMPORT, json={"csv": sheet, "dry_run": False}).json()

    assert body["saved"] is False
    assert body["errors"] == 4
    problems = {r["line"]: " ".join(r["messages"]) for r in body["rows"] if r["status"] == "error"}
    assert "isn't a unit we track" in problems[3]
    assert "“lots” isn't a number" in problems[4]
    assert "No item name" in problems[5]
    assert "Also on line 2" in problems[6]
    assert db.query(InventoryItem).count() == 0


def test_items_already_tracked_are_skipped_or_updated(client, db):
    _set_up(client, db)
    client.post(ITEMS, json={"name": "Flour", "unit": "lb", "quantity_on_hand": 10, "reorder_threshold": 2})
    sheet = "Item,Unit,Qty,Par\nflour,lb,25,5\nRice,kg,3,1\n"

    skipped = client.post(IMPORT, json={"csv": sheet, "dry_run": False}).json()
    assert (skipped["new"], skipped["skipped"], skipped["saved"]) == (1, 1, True)
    assert {i["name"]: i["quantity_on_hand"] for i in client.get(ITEMS).json()}["Flour"] == 10

    updated = client.post(IMPORT, json={"csv": sheet, "update_existing": True, "dry_run": False}).json()
    assert updated["updated"] == 2  # flour and, now, rice
    stock = {i["name"]: i for i in client.get(ITEMS).json()}
    assert (stock["Flour"]["quantity_on_hand"], stock["Flour"]["reorder_threshold"]) == (25, 5)
    # The new level is recorded as a count, not a silent overwrite.
    assert db.query(InventoryAdjustment).filter_by(reason="count", note="Imported").count() == 1


@pytest.mark.parametrize(
    "sheet,message",
    [
        ("", "empty"),
        ("Qty,Cost\n1,2\n", "needs a column for the item's name and unit"),
        ("Item,Unit\n", "no items"),
        ("Item,Unit\n" + "".join(f"Thing {i},each\n" for i in range(2001)), "more than 2,000"),
    ],
)
def test_unusable_files_are_turned_away(client, db, sheet, message):
    _set_up(client, db)
    response = client.post(IMPORT, json={"csv": sheet})
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_semicolons_and_excel_byte_order_marks_are_fine(client, db):
    _set_up(client, db)
    sheet = "﻿Product;Unit of measure;Stock\nBasil;bunch;6\n"
    body = client.post(IMPORT, json={"csv": sheet}).json()
    assert body["rows"][0]["name"] == "Basil" and body["rows"][0]["quantity_on_hand"] == 6


def test_importing_is_for_owners_and_admins(client, db):
    _set_up(client, db)
    member = _member(client, db)
    assert member.post(IMPORT, json={"csv": SHEET}).status_code == 403


def test_suppliers_are_matched_only_within_the_restaurant(client, db):
    _set_up(client, db)
    other, _ = _second_restaurant(db)
    db.add(Supplier(name="Green Valley", lead_time_days=1, restaurant_id=other.id))
    db.commit()

    body = client.post(IMPORT, json={"csv": SHEET, "dry_run": False}).json()
    # Their Green Valley isn't ours: a new one is created here instead.
    assert "Green Valley" in body["new_suppliers"]
    mine = client.get("/api/v1/auth/me").json()["restaurant_id"]
    assert db.query(Supplier).filter_by(name="Green Valley", restaurant_id=mine).count() == 1
    assert db.query(InventoryItem).filter(InventoryItem.restaurant_id != mine).count() == 0
