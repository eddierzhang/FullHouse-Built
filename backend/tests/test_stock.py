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
    start = engine.local_today(db, rid)
    engine.deplete(db, rid, start)  # tracking begins today
    sold = [12, 9, 15, 20, 31, 34, 22]
    moved = 0
    for offset, qty in enumerate(sold):  # each day's sales arrive, and the job runs, that day
        _sale(db, rid, start + timedelta(days=offset), qty)
        moved += engine.deplete(db, rid, start + timedelta(days=offset))["moved"]
    db.commit()

    plates = sum(sold)
    assert moved == 14  # two ingredients, seven days
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
    today = engine.local_today(db, rid)
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
    today = engine.local_today(db, rid)
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
    today = engine.local_today(db, rid)
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
    today = engine.local_today(db, rid)
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
    assert report["items"][0]["name"] == "Mozzarella" and report["items"][0]["unexplained"] == -20
    assert 150 < report["missing_dollars"] < 400 and report["wasted_dollars"] > 0
    assert len(engine.waste_log(db, rid)) == 5
    # Tracking starts today: loading the demo again takes nothing more off.
    assert engine.deplete(db, rid)["moved"] == 0


# --- one source of sales, units that don't convert, and POS items that are not dishes -----------------


def test_a_sale_in_both_the_app_and_the_pos_comes_off_the_shelf_once(client, db):
    rid, flour, cheese, pizza = _kitchen(client, db)
    today = engine.local_today(db, rid)
    engine.deplete(db, rid, today)  # tracking begins; no POS sales yet
    db.commit()
    order = {"channel": "dine_in", "items": [{"menu_item_id": pizza.id, "qty": 4}]}

    # No POS sales: orders taken in FullHouse are the sales, and deduct as placed.
    assert client.get(f"{API}/status").json()["sales_source"] == "app"
    assert client.post("/api/v1/restaurant/orders", json=order).status_code in (200, 201)
    db.refresh(flour)
    assert flour.quantity_on_hand == 98

    # The POS starts reporting the same sales: from then on only its lines deduct.
    _sale(db, rid, today, 10)
    assert client.get(f"{API}/status").json()["sales_source"] == "pos"
    assert client.post("/api/v1/restaurant/orders", json=order).status_code in (200, 201)
    db.refresh(flour)
    assert flour.quantity_on_hand == 98  # recorded, not deducted again
    engine.deplete(db, rid, today)
    db.commit()
    db.refresh(flour)
    assert flour.quantity_on_hand == 93  # the ten the POS reported

    # An owner who really does take orders here can pin it; then the POS lines are left alone.
    assert client.put(f"{API}/settings", json={"sales_source": "app"}).json() == {"sales_source": "app", "sales_source_pinned": True}
    _sale(db, rid, today, 30)
    assert engine.deplete(db, rid, today)["moved"] == 0
    assert client.put(f"{API}/settings", json={}).json()["sales_source"] == "pos"
    assert ledger.out_of_balance(db, rid) == []


def test_a_restaurants_own_conversion_lets_an_invoice_in_pounds_book_an_item_counted_in_bunches(client, db):
    from app.prices.models import PriceItem
    from app.prices.stock import units_factor
    from app.voice import parser

    rid, *_ = _kitchen(client, db)
    basil = InventoryItem(restaurant_id=rid, name="Basil", unit="bunch", quantity_on_hand=10, unit_cost=2.4)
    db.add(basil)
    db.flush()
    by_the_pound = PriceItem(restaurant_id=rid, name="Basil, 1 lb", category="food", unit="lb", pack_unit="each", pack_size=1, inventory_item_id=basil.id)
    db.add(by_the_pound)
    db.commit()

    assert units_factor(by_the_pound, basil) is None
    assert [(n["name"], n["unit"], n["other_unit"]) for n in client.get(f"{API}/status").json()["needs_conversion"]] == [("Basil", "bunch", "lb")]

    saved = client.put(f"{API}/items/{basil.id}/conversion", json={"other_unit": "LB", "factor": 8}).json()
    assert saved["needs_conversion"] == [] and saved["conversions"] == [
        {"inventory_item_id": basil.id, "name": "Basil", "unit": "bunch", "other_unit": "lb", "factor": 8}]
    assert units_factor(by_the_pound, basil) == 8
    spoken = parser.parse("basil two pounds", [{"id": basil.id, "name": "Basil", "unit": "bunch", "conversions": {"lb": 8}}])
    assert [(l.quantity, l.sure) for l in spoken.lines] == [(16, True)]
    assert client.put(f"{API}/items/{basil.id}/conversion", json={"other_unit": "bunch", "factor": 2}).status_code == 422
    assert client.put(f"{API}/items/{basil.id}/conversion", json={"other_unit": "lb"}).json()["conversions"] == []


def test_a_pos_item_that_is_not_a_dish_can_use_an_ingredient_stand_for_a_dish_or_be_ignored(client, db):
    rid, flour, cheese, pizza = _kitchen(client, db)
    today = engine.local_today(db, rid)
    engine.deplete(db, rid, today)
    _sale(db, rid, today, 6, name="Extra Cheese", pos="MOD-CHEESE")
    _sale(db, rid, today, 3, name="Marg Pie", pos="MARGPIE")
    _sale(db, rid, today, 2, name="Gift Card", pos="GIFT")
    engine.deplete(db, rid, today)
    db.commit()
    assert sorted(u["pos_item_name"] for u in client.get(f"{API}/status").json()["unmapped"]) == ["Extra Cheese", "Gift Card", "Marg Pie"]

    left = client.put(f"{API}/pos-items", json={"pos_item_name": "Extra Cheese", "lines": [{"inventory_item_id": cheese.id, "quantity": 0.1}]}).json()
    client.put(f"{API}/pos-items", json={"pos_item_name": "marg pie", "menu_item_id": pizza.id})
    after = client.put(f"{API}/pos-items", json={"pos_item_name": "Gift Card", "ignore": True}).json()
    assert len(left["unmapped"]) == 2 and after["unmapped"] == []
    assert client.put(f"{API}/pos-items", json={"pos_item_name": "Extra Cheese", "ignore": True, "menu_item_id": pizza.id}).status_code == 422

    engine.deplete(db, rid, today)
    db.commit()
    db.refresh(cheese), db.refresh(flour)
    # Six extra cheese at 0.1 lb, and three pizzas under another name through the pizza's recipe.
    assert cheese.quantity_on_hand == pytest.approx(40 - 6 * 0.1 - 3 * 0.3)
    assert flour.quantity_on_hand == pytest.approx(100 - 3 * 0.5)
    assert engine.deplete(db, rid, today)["moved"] == 0

    other, b = _second_restaurant(db)
    flags.set_flag(db, other.id, flags.V3_STOCK, True)
    db.commit()
    assert b.put(f"{API}/pos-items", json={"pos_item_name": "X", "menu_item_id": pizza.id}).status_code == 404
    assert b.put(f"{API}/pos-items", json={"pos_item_name": "X", "lines": [{"inventory_item_id": cheese.id, "quantity": 1}]}).status_code == 404
    assert b.put(f"{API}/items/{cheese.id}/conversion", json={"other_unit": "kg", "factor": 2}).status_code == 404


# --- from the correctness review --------------------------------------------------------------------


def test_changing_a_recipe_does_not_reach_back_and_re_deduct_settled_days(client, db):
    rid, flour, cheese, pizza = _kitchen(client, db)
    start = engine.local_today(db, rid) - timedelta(days=10)
    engine.deplete(db, rid, start)
    for offset in range(10):
        _sale(db, rid, start + timedelta(days=offset), 10)
        engine.deplete(db, rid, start + timedelta(days=offset))
    db.commit()
    db.refresh(cheese)
    assert cheese.quantity_on_hand == pytest.approx(40 - 100 * 0.3)

    db.query(RecipeItem).filter_by(menu_item_id=pizza.id, inventory_item_id=cheese.id).update({"quantity": 0.4})
    db.commit()
    engine.deplete(db, rid, start + timedelta(days=9))
    db.commit()
    db.refresh(cheese)
    # Only the days still open (the last few) are re-read at the new recipe; the earlier ones stand.
    reread = engine.DEPLETE_WINDOW_DAYS + 1
    assert cheese.quantity_on_hand == pytest.approx(40 - 100 * 0.3 - reread * 10 * 0.1)


def test_a_sale_dated_before_a_count_and_synced_after_it_is_not_taken_off_twice(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)
    today = engine.local_today(db, rid)
    engine.deplete(db, rid, today - timedelta(days=2))
    # The shelf is counted today; yesterday's sales export only arrives afterwards.
    engine.count_now(db, rid, [{"inventory_item_id": cheese.id, "counted_qty": 25}], client_key="count-after-0001")
    _sale(db, rid, today - timedelta(days=1), 20)
    engine.deplete(db, rid, today)
    db.commit()
    db.refresh(cheese), db.refresh(flour)
    assert cheese.quantity_on_hand == 25      # the count already had yesterday's sales in it
    assert flour.quantity_on_hand == 90       # flour was not counted, so its sales still come off


def test_a_delivery_between_counting_and_posting_survives_the_count(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)
    sheet = client.post(f"{API}/counts", json={}).json()
    client.put(f"{API}/counts/{sheet['id']}/lines", json={"lines": [{"inventory_item_id": cheese.id, "counted_qty": 30}]})
    ledger.move(db, cheese, 20, "delivery", note="Arrived while the count was open")
    db.commit()

    posted = client.post(f"{API}/counts/{sheet['id']}/post").json()

    db.refresh(cheese)
    assert cheese.quantity_on_hand == 50  # the 30 counted, and the 20 that came after
    line = next(l for l in posted["lines"] if l["inventory_item_id"] == cheese.id)
    assert (line["expected_qty"], line["counted_qty"]) == (40, 30)  # ten were missing when it was counted
    assert ledger.out_of_balance(db, rid) == []


def test_waste_on_an_item_already_below_zero_takes_nothing_and_an_undone_entry_leaves_the_report(client, db):
    rid, flour, cheese, _ = _kitchen(client, db)
    ledger.move(db, cheese, -45, "sale", source_key="sale:test:ran-negative")
    db.commit()
    assert cheese.quantity_on_hand == -5

    out = client.post(f"{API}/waste", json={"inventory_item_id": cheese.id, "quantity": 2, "reason": "dropped"}).json()
    db.refresh(cheese)
    assert (out["taken"], out["dollars"], cheese.quantity_on_hand) == (0, 0, -5)

    # Fifty pounds of flour logged by mistake, then undone: it is not reported as wasted.
    engine.record_waste(db, flour, 50, "spoiled")
    ledger.move(db, flour, 50, "revert", note="Undone")
    engine.rollup(db, rid)
    db.commit()
    assert engine.variance(db, rid, 28)["wasted_dollars"] == 0


def test_a_pos_item_id_with_a_hash_in_it_still_has_its_later_sales_taken_off(client, db):
    rid, flour, *_ = _kitchen(client, db)
    today = engine.local_today(db, rid)
    engine.deplete(db, rid, today)
    _sale(db, rid, today, 4, pos="Combo #2")
    engine.deplete(db, rid, today)
    _sale(db, rid, today, 10, pos="Combo #2")
    engine.deplete(db, rid, today)
    db.commit()
    db.refresh(flour)
    assert flour.quantity_on_hand == 95
