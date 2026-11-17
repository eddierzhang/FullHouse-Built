"""Phase 3: stock that tracks itself. Sales come off the shelf through recipes, counts and waste go through
the same ledger, and every movement is recorded once however often it is replayed."""

import random
from datetime import date, timedelta

import pytest

from app.demo import reset_demo_data
from app.platform import flags, jobs
from app.restaurant.models import InventoryAdjustment, InventoryItem, MenuItem, RecipeItem, Restaurant
from app.sales.models import SalesItem
from app.stock import engine, ledger
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/stock"


def _kitchen(client, db):
    """A restaurant with stock tracking on, flour and cheese on the shelf, and a pizza that uses both."""
    restaurant = _set_up(client, db)
    rid = restaurant["id"]
    flags.set_flag(db, rid, flags.V3_STOCK, True)
    flour = InventoryItem(restaurant_id=rid, name="Flour", unit="lb", quantity_on_hand=100, unit_cost=0.5)
    cheese = InventoryItem(restaurant_id=rid, name="Mozzarella", unit="lb", quantity_on_hand=40, unit_cost=6.0)
    pizza = MenuItem(restaurant_id=rid, name="Margherita Pizza", category="Entree", price=14.0, cost=3.0)
    db.add_all([flour, cheese, pizza])
    db.flush()
    db.add_all([RecipeItem(menu_item_id=pizza.id, inventory_item_id=flour.id, quantity=0.5),
                RecipeItem(menu_item_id=pizza.id, inventory_item_id=cheese.id, quantity=0.3)])
    db.commit()
    return rid, flour, cheese, pizza


def _sale(db, rid, day, qty, name="Margherita Pizza", pos="MARG"):
    row = db.query(SalesItem).filter_by(restaurant_id=rid, date=day, pos_item_id=pos, source="square").first()
    if row is None:
        row = SalesItem(restaurant_id=rid, date=day, pos_item_id=pos, pos_item_name=name, qty=qty, net=qty * 14.0, source="square")
        db.add(row)
    else:
        row.qty = qty
    db.commit()
    return row


def test_acceptance_seven_days_of_sales_deplete_stock_through_recipes_and_a_replay_changes_nothing(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)
    start = date.today()
    engine.deplete(db, rid, start)  # tracking begins today
    sold = [12, 9, 15, 20, 31, 34, 22]
    for offset, qty in enumerate(sold):
        _sale(db, rid, start + timedelta(days=offset), qty)

    result = engine.deplete(db, rid, start + timedelta(days=6))
    db.commit()

    plates = sum(sold)
    assert result["moved"] == 14  # two ingredients, seven days
    assert flour.quantity_on_hand == pytest.approx(100 - plates * 0.5, rel=1e-3)
    assert cheese.quantity_on_hand == pytest.approx(40 - plates * 0.3, rel=1e-3)
    # I12: the same sales again move nothing.
    before = db.query(InventoryAdjustment).count()
    assert engine.deplete(db, rid, start + timedelta(days=6))["moved"] == 0
    assert db.query(InventoryAdjustment).count() == before
    # I11: the ledger is the truth.
    assert ledger.out_of_balance(db, rid) == []


def test_sales_from_before_tracking_began_are_left_alone_and_a_revised_day_moves_only_the_difference(client, db):
    rid, flour, _, _ = _kitchen(client, db)
    today = date.today()
    _sale(db, rid, today - timedelta(days=1), 50)  # already in the opening figures
    assert engine.deplete(db, rid, today)["moved"] == 0
    assert flour.quantity_on_hand == 100

    _sale(db, rid, today, 10)  # lunch
    engine.deplete(db, rid, today)
    assert flour.quantity_on_hand == 95
    _sale(db, rid, today, 26)  # the same day's line after dinner
    engine.deplete(db, rid, today)
    db.commit()
    assert flour.quantity_on_hand == 87
    assert sorted(a.source_key.split("#")[-1] for a in db.query(InventoryAdjustment).filter_by(inventory_item_id=flour.id, reason="sale")) != []
    assert db.query(InventoryAdjustment).filter_by(inventory_item_id=flour.id, reason="sale").count() == 2
    assert ledger.out_of_balance(db, rid) == []


def test_yield_takes_more_off_the_shelf_and_a_dish_with_no_recipe_is_reported_not_guessed(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)
    today = date.today()
    engine.deplete(db, rid, today)
    cheese.yield_pct = 75  # a quarter is trimmed away
    db.commit()
    _sale(db, rid, today, 10)
    _sale(db, rid, today, 4, name="Affogato", pos="AFFO")

    engine.deplete(db, rid, today)
    db.commit()

    assert cheese.quantity_on_hand == pytest.approx(40 - 10 * 0.3 / 0.75)
    assert [(u["pos_item_name"], u["why"]) for u in engine.unmapped(db, rid)] == [("Affogato", "no dish on the menu has this name")]


def test_a_count_records_the_difference_once_and_uncounted_lines_change_nothing(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)

    sheet = client.post(f"{API}/counts", json={}).json()
    assert [(l["item_name"], l["expected_qty"], l["counted_qty"]) for l in sheet["lines"]] == [("Flour", 100, None), ("Mozzarella", 40, None)]
    assert client.post(f"{API}/counts", json={}).json()["id"] == sheet["id"]  # the open sheet is carried on

    saved = client.put(f"{API}/counts/{sheet['id']}/lines", json={"lines": [{"inventory_item_id": cheese.id, "counted_qty": 36.5}]}).json()
    assert saved["summary"] == {"lines": 2, "counted": 1, "difference_dollars": -21.0, "short": 1, "over": 0}
    db.refresh(cheese)
    assert cheese.quantity_on_hand == 40  # nothing moves until it is posted

    posted = client.post(f"{API}/counts/{sheet['id']}/post").json()
    again = client.post(f"{API}/counts/{sheet['id']}/post").json()
    db.refresh(cheese), db.refresh(flour)
    assert posted["status"] == again["status"] == "posted"
    assert (cheese.quantity_on_hand, flour.quantity_on_hand) == (36.5, 100)
    counted = db.query(InventoryAdjustment).filter_by(reason="count").all()
    assert [(a.change, a.source_key) for a in counted] == [(-3.5, f"count:{sheet['id']}:{cheese.id}")]
    assert client.put(f"{API}/counts/{sheet['id']}/lines", json={"lines": []}).status_code == 409
    assert ledger.out_of_balance(db, rid) == []


def test_a_count_synced_twice_from_a_phone_posts_once(client, db):
    rid, flour, _, _ = _kitchen(client, db)
    body = {"client_key": "phone-7f3a-2026-11-17", "lines": [{"inventory_item_id": flour.id, "counted_qty": 92}]}

    first = client.post(f"{API}/counts/now", json=body).json()
    second = client.post(f"{API}/counts/now", json=body).json()

    db.refresh(flour)
    assert first["id"] == second["id"] and flour.quantity_on_hand == 92
    assert db.query(InventoryAdjustment).filter_by(reason="count").count() == 1


def test_waste_is_recorded_once_with_its_reason_and_cannot_take_more_than_is_there(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)
    headers = {"Idempotency-Key": "waste-tap-1"}
    body = {"inventory_item_id": cheese.id, "quantity": 2, "reason": "dropped", "note": "tub tipped"}

    first = client.post(f"{API}/waste", json=body, headers=headers).json()
    client.post(f"{API}/waste", json=body, headers=headers)
    db.refresh(cheese)
    assert (first["taken"], first["dollars"], cheese.quantity_on_hand) == (2, 12.0, 38)

    over = client.post(f"{API}/waste", json={"inventory_item_id": flour.id, "quantity": 500, "reason": "spoiled"}).json()
    assert (over["taken"], over["on_hand"]) == (100, 0)
    assert client.post(f"{API}/waste", json={**body, "reason": "vanished"}).status_code == 422
    log = client.get(f"{API}/waste").json()["entries"]
    assert [(e["item_name"], e["quantity"], e["reason"], e["note"]) for e in log] == [("Flour", 100, "spoiled", None), ("Mozzarella", 2, "dropped", "tub tipped")]


def test_the_variance_report_separates_sales_waste_and_what_a_count_found_missing(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)
    today = date.today()
    engine.deplete(db, rid, today)
    _sale(db, rid, today, 20)
    engine.deplete(db, rid, today)
    engine.record_waste(db, cheese, 1, "spoiled")
    engine.count_now(db, rid, [{"inventory_item_id": cheese.id, "counted_qty": 30}], client_key="count-key-0001")
    engine.rollup(db, rid, today=today)
    db.commit()

    report = client.get(f"{API}/variance").json()
    rows = {r["name"]: r for r in report["items"]}

    assert (rows["Mozzarella"]["used"], rows["Mozzarella"]["wasted"], rows["Mozzarella"]["unexplained"]) == (6.0, 1.0, -3.0)
    assert rows["Mozzarella"]["unexplained_dollars"] == -18.0 and rows["Mozzarella"]["pct_of_use"] == 50.0
    # Flour was sold but never counted: it has an expected figure and no shrink.
    assert (rows["Flour"]["used"], rows["Flour"]["unexplained"]) == (10.0, None)
    assert (report["missing_dollars"], report["wasted_dollars"], report["counted"], report["not_counted"]) == (18.0, 6.0, 1, 1)


def test_the_ledger_is_the_truth_under_any_sequence_of_movements(client, db):
    """I11, as a property: whatever mix of sales, deliveries, waste and counts, replayed or not, on hand is the ledger sum."""
    rid, flour, cheese, _ = _kitchen(client, db)
    rng = random.Random(7)
    keys = []
    for step in range(120):
        item = rng.choice([flour, cheese])
        kind = rng.choice(["sale", "delivery", "waste", "count", "replay"])
        if kind == "replay" and keys:
            key, change = rng.choice(keys)
            assert ledger.move(db, item, change, "sale", source_key=key) is False or key.split(":")[1] != item.id
        elif kind == "count":
            ledger.set_to(db, item, round(rng.uniform(0, 120), 2), source_key=f"count:{step}:{item.id}")
        elif kind == "waste":
            ledger.move(db, item, -round(rng.uniform(0.1, 30), 2), "waste", allow_negative=False)
        else:
            change = round(rng.uniform(0.1, 25), 2) * (1 if kind == "delivery" else -1)
            key = f"t:{item.id}:{step}"
            ledger.move(db, item, change, kind, source_key=key)
            keys.append((key, change))
    db.commit()
    assert ledger.out_of_balance(db, rid) == []
    assert ledger.balance(db, flour) == pytest.approx(flour.quantity_on_hand)


def test_new_sales_queue_the_stock_job_which_depletes_rolls_up_and_tells_once(client, db):
    from app.notify.models import Notification
    from app.platform import events

    rid, flour, cheese, _ = _kitchen(client, db)
    today = date.today()
    engine.deplete(db, rid, today)
    cheese.quantity_on_hand = 0.1  # not enough for one plate
    db.commit()
    _sale(db, rid, today, 8)
    events.emit(db, rid, "sales_items.synced", "connections", "square", {"rows": 1})
    db.commit()

    assert jobs.drain(db) >= 1
    db.refresh(flour)
    assert flour.quantity_on_hand == 96
    titles = [n.title for n in db.query(Notification).filter_by(restaurant_id=rid)]
    assert titles == ["86 Margherita Pizza"]
    engine.run(db, rid, today)
    assert db.query(Notification).filter_by(restaurant_id=rid).count() == 1  # told once a day, not every half hour


def test_areas_order_the_count_sheet_and_everything_is_behind_the_flag_and_the_restaurant(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)

    areas = client.post(f"{API}/areas", json={"name": "Walk-in"}).json()
    assert client.post(f"{API}/areas", json={"name": "walk-in"}).status_code == 409
    walk_in = areas[0]["id"]
    assert client.put(f"{API}/items/{cheese.id}/place", json={"storage_area_id": walk_in, "shelf_order": 2, "yield_pct": 90}).json()["yield_pct"] == 90
    sheet = client.post(f"{API}/counts", json={"storage_area_id": walk_in}).json()
    assert (sheet["area_name"], [l["item_name"] for l in sheet["lines"]]) == ("Walk-in", ["Mozzarella"])
    assert [l["item_name"] for l in client.post(f"{API}/counts", json={"storage_area_id": "unplaced"}).json()["lines"]] == ["Flour"]

    other, b = _second_restaurant(db)
    assert b.get(f"{API}/areas").status_code == 404  # the flag is off there
    flags.set_flag(db, other.id, flags.V3_STOCK, True)
    db.commit()
    assert b.get(f"{API}/areas").json() == []
    assert b.get(f"{API}/counts/{sheet['id']}").status_code == 404
    assert b.post(f"{API}/waste", json={"inventory_item_id": cheese.id, "quantity": 1, "reason": "spoiled"}).status_code == 404
    assert b.put(f"{API}/items/{cheese.id}/place", json={"yield_pct": 50}).status_code == 404
    mine = b.post(f"{API}/counts/now", json={"client_key": "other-phone-0001", "lines": [{"inventory_item_id": cheese.id, "counted_qty": 0}]}).json()
    db.refresh(cheese)
    assert mine["lines"] == [] and cheese.quantity_on_hand == 40  # another restaurant's item is not theirs to count


def test_the_demo_stock_history_adds_up_and_last_weeks_count_found_some_missing(db):
    reset_demo_data(db)
    rid = db.query(Restaurant).one().id

    assert ledger.out_of_balance(db, rid) == []
    report = engine.variance(db, rid, 28)
    assert report["counted"] == 8 and report["not_counted"] == 4
    assert report["items"][0]["name"] == "Chicken Breast" and report["items"][0]["unexplained"] == -16
    assert 150 < report["missing_dollars"] < 400 and report["wasted_dollars"] > 0
    assert len(engine.waste_log(db, rid)) == 5
    # Tracking starts today: loading the demo again takes nothing more off.
    assert engine.deplete(db, rid)["moved"] == 0
