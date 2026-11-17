"""Phase 2: search, the savings and cost charts, and what the what-if tools read."""

from app.demo import reset_demo_data
from app.insights import engine
from app.platform import flags
from app.restaurant.models import InventoryItem, Restaurant
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up


def _demo(db):
    reset_demo_data(db)
    return db.query(Restaurant).one().id


def test_savings_add_up_week_by_week_and_count_what_was_acted_on(db):
    restaurant_id = _demo(db)

    out = engine.savings(db, restaurant_id)

    assert len(out["weeks"]) == 6  # five weeks of history and this one
    assert [w["week_start"] for w in out["weeks"]] == sorted(w["week_start"] for w in out["weeks"])
    running = 0.0
    for week in out["weeks"]:
        running = round(running + week["found"], 2)
        assert week["found_to_date"] == running
        assert 0 <= week["acted"] <= week["found"]
    assert out["found_to_date"] == running
    # The chicken rise was raised with the rep nine days ago: it counts in the weeks it was on the digest before then.
    assert out["acted_to_date"] > 0
    assert out["open_this_week"] == round(out["weeks"][-1]["found"] - out["weeks"][-1]["acted"], 2)


def test_prime_cost_is_the_weeks_the_job_worked_out_oldest_first(db):
    restaurant_id = _demo(db)

    weeks = engine.prime_cost(db, restaurant_id)

    assert [w["week_of"] for w in weeks] == sorted(w["week_of"] for w in weeks) and len(weeks) == 3
    for week in weeks:
        assert abs(week["food_pct"] + week["labor_pct"] + week["paper_pct"] - week["prime_pct"]) < 0.06  # each is rounded to two places


def test_spend_splits_the_same_total_by_supplier_and_by_category(db):
    restaurant_id = _demo(db)

    out = engine.spend(db, restaurant_id, 28)

    assert out["total_now"] > 0 and out["total_before"] > 0
    assert round(sum(r["now"] for r in out["by_supplier"]), 2) == out["total_now"]
    assert round(sum(r["now"] for r in out["by_category"]), 2) == out["total_now"]
    assert out["by_supplier"][0]["label"] == "Harbor Dairy Co."  # the mozzarella


def test_stock_outlook_says_how_long_each_item_lasts_and_what_is_on_the_way(db):
    restaurant_id = _demo(db)

    out = engine.stock_outlook(db, restaurant_id)
    by_name = {i["name"]: i for i in out["items"]}

    tomatoes = by_name["Tomatoes"]
    assert tomatoes["daily_use"] > 0 and tomatoes["days_left"] == round(tomatoes["on_hand"] / tomatoes["daily_use"], 1)
    assert tomatoes["lead_time_days"] == 1  # Green Valley Farms
    assert [o["quantity"] for o in by_name["Flour"]["incoming"]] == [500]
    # The mozzarella was due yesterday: it is drawn as arriving today, and marked late.
    assert by_name["Mozzarella"]["incoming"][0]["late"] is True
    assert len(out["weekday_factors"]) == 7 and max(out["weekday_factors"]) > 1 > min(out["weekday_factors"])


def test_switch_options_list_each_suppliers_latest_price_cheapest_first(db):
    restaurant_id = _demo(db)

    options = engine.switch_options(db, restaurant_id)

    tomatoes = next(o for o in options if o["item_name"].startswith("Tomatoes"))
    assert [(s["name"], s["per_pack"]) for s in tomatoes["suppliers"]] == [("Sysco Foods", 27.6), ("Green Valley Farms", 31.2)]
    assert tomatoes["weekly_packs"] > 0
    assert all(len(o["suppliers"]) >= 2 for o in options)


def test_search_finds_items_suppliers_invoices_and_dishes_and_nothing_for_one_letter(db):
    restaurant_id = _demo(db)

    kinds = {(h["kind"], h["title"]) for h in engine.search(db, restaurant_id, "tomato")}
    assert ("stock", "Tomatoes") in kinds and ("price", "Tomatoes, 25 lb case") in kinds
    assert {h["kind"] for h in engine.search(db, restaurant_id, "sysco")} >= {"supplier", "invoice"}
    assert [h["title"] for h in engine.search(db, restaurant_id, "tiramisu")] == ["Tiramisu"]
    assert engine.search(db, restaurant_id, "t") == []


def test_the_routes_are_behind_the_flag_and_never_show_another_restaurants_rows(client, db):
    restaurant = _set_up(client, db)
    assert client.get("/api/v1/insights/search?q=flour").status_code == 404  # the flag is off

    flags.set_flag(db, restaurant["id"], flags.V3_NAV, True)
    db.add(InventoryItem(restaurant_id=restaurant["id"], name="Flour", unit="lb", quantity_on_hand=10))
    db.commit()
    assert [h["title"] for h in client.get("/api/v1/insights/search?q=flour").json()] == ["Flour"]

    other, b = _second_restaurant(db)
    flags.set_flag(db, other.id, flags.V3_NAV, True)
    db.commit()
    assert b.get("/api/v1/insights/search?q=flour").json() == []
    assert b.get("/api/v1/insights/stock-outlook").json()["items"] == []
    for path in ("savings", "prime-cost", "spend", "switch-options"):
        assert b.get(f"/api/v1/insights/{path}").status_code == 200
