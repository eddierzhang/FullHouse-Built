"""Module A against the database (spec sections 5, 11 and 12): recost on a new price, one current row, the alert
rule, the reprice proposal and its cap, the outlier guard, recipe saves, and the routes the pages read."""

import time
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.agents.models import AgentAction
from app.margin_labor import menu_costs, menu_costs_read
from app.margin_labor.models import DishCost, Finding, PosItem, RestaurantSettings
from app.platform import flags, jobs
from app.platform.models import Event
from app.prices.models import Invoice, InvoiceLine, PriceItem
from app.recipes.models import CONFIRMED, PROPOSED, Recipe, RecipeLine
from app.restaurant.models import Supplier
from app.sales.models import SalesItem
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/margin-labor"


def _invoice(db, rid, supplier, day, lines):
    """lines: (item, packs, dollars a pack)."""
    invoice = Invoice(restaurant_id=rid, supplier_id=supplier.id, invoice_date=day, status="complete", total=0)
    db.add(invoice)
    db.flush()
    for number, (item, qty, price) in enumerate(lines, start=1):
        db.add(InvoiceLine(restaurant_id=rid, invoice_id=invoice.id, supplier_id=supplier.id, line_no=number, description=item.name, qty=qty,
                           unit_price=price, extended=qty * price, item_id=item.id, pack_size=item.pack_size))
    db.commit()
    return invoice


class Kitchen:
    """The spec's burger: beef $0.012 a g, bun $0.45, onion $0.002 a g at 85% yield, house sauce $3.00 a 500 g batch."""

    def __init__(self, client, db, sold_a_day=5, flag=True):
        self.db = db
        self.rid = _set_up(client, db)["id"]
        if flag:
            flags.set_flag(db, self.rid, flags.ML_MENU_COSTS, True)
        self.today = menu_costs.load(db, self.rid).today
        self.farm = Supplier(name="Farm", restaurant_id=self.rid, lead_time_days=2)
        db.add(self.farm)
        db.flush()
        mk = lambda name, unit, pack: PriceItem(restaurant_id=self.rid, name=name, unit=unit, pack_unit="case", pack_size=pack)  # noqa: E731
        self.beef, self.bun, self.onion, self.base = mk("Ground beef", "g", 1000), mk("Bun", "ct", 12), mk("Onion", "g", 1000), mk("Sauce base", "g", 1000)
        db.add_all([self.beef, self.bun, self.onion, self.base])
        db.flush()
        self.first = _invoice(db, self.rid, self.farm, self.today - timedelta(days=40),
                              [(self.beef, 2, 12.00), (self.bun, 3, 5.40), (self.onion, 1, 2.00), (self.base, 1, 6.00)])
        self.sauce = Recipe(restaurant_id=self.rid, name="House sauce", yield_qty=500, yield_unit="g", status=CONFIRMED)
        self.burger = Recipe(restaurant_id=self.rid, name="Burger", pos_item_id="BURGER", yield_qty=1, yield_unit="plate", status=CONFIRMED)
        db.add_all([self.sauce, self.burger])
        db.flush()
        db.add(RecipeLine(restaurant_id=self.rid, recipe_id=self.sauce.id, position=0, item_id=self.base.id, qty=500, unit="g"))
        db.add_all([
            RecipeLine(restaurant_id=self.rid, recipe_id=self.burger.id, position=0, item_id=self.beef.id, qty=150, unit="g"),
            RecipeLine(restaurant_id=self.rid, recipe_id=self.burger.id, position=1, item_id=self.bun.id, qty=1, unit="each"),
            RecipeLine(restaurant_id=self.rid, recipe_id=self.burger.id, position=2, item_id=self.onion.id, qty=30, unit="g", yield_pct=D("0.85")),
            RecipeLine(restaurant_id=self.rid, recipe_id=self.burger.id, position=3, sub_recipe_id=self.sauce.id, qty=20, unit="g"),
        ])
        db.add(PosItem(restaurant_id=self.rid, pos_item_id="BURGER", name="Burger", category="Mains", price=D("12.00")))
        for back in range(1, 29):
            if sold_a_day:
                db.add(SalesItem(restaurant_id=self.rid, date=self.today - timedelta(days=back), pos_item_id="BURGER", pos_item_name="Burger",
                                 qty=sold_a_day, net=sold_a_day * 12.0, source="square"))
        db.commit()
        jobs.drain(db, queues=("derive",))
        db.commit()

    def current(self, recipe=None):
        self.db.expire_all()
        return self.db.query(DishCost).filter_by(recipe_id=(recipe or self.burger).id, is_current=True).one()

    def beef_at(self, dollars_a_kg, days_ago=1):
        _invoice(self.db, self.rid, self.farm, self.today - timedelta(days=days_ago), [(self.beef, 2, dollars_a_kg)])
        ran = jobs.drain(self.db, queues=("derive",))
        self.db.commit()
        return ran

    def finding(self, status="open"):
        return self.db.query(Finding).filter_by(restaurant_id=self.rid, type="menu_margin", subject_id=self.burger.id, status=status).first()


def test_golden_the_burger_is_recosted_from_invoices_to_2_4406_and_20_3_percent(client, db):
    k = Kitchen(client, db)
    cost = k.current()
    assert (cost.plate_cost, cost.batch_cost, cost.menu_price, cost.food_cost_pct, cost.margin_dollars) == (
        D("2.4406"), D("2.4406"), D("12.0000"), D("0.2034"), D("9.5594"))
    assert (cost.lines_priced, cost.lines_total, cost.unpriced, cost.calc_version) == (4, 4, [], "a1")
    assert k.current(k.sauce).batch_cost == D("3.0000")
    # The basis keeps, per line, the price point, the unit price, the conversion factor and the yield that were used.
    beef, bun, onion, sauce = cost.basis["lines"]
    line = db.query(InvoiceLine).filter_by(item_id=k.beef.id).one()
    assert (beef["price_point_id"], beef["invoice_id"], beef["unit_price"], beef["conversion_factor"], beef["yield_pct"], beef["line_cost"]) == (
        line.id, k.first.id, "0.012", "1", "1", "1.8000")
    assert (onion["yield_pct"], onion["purchased_qty"], onion["line_cost"]) == ("0.8500", "35.29411765", "0.0706")
    assert (sauce["kind"], sauce["sub_batch_cost"], sauce["line_cost"], bun["line_cost"]) == ("sub_recipe", "3.0000", "0.1200", "0.4500")
    assert cost.basis["menu_price"]["source"] == "pos_items" and k.finding() is None  # 20.3% is under the 30% target


def test_running_recost_twice_with_the_same_inputs_leaves_the_same_rows(client, db):
    k = Kitchen(client, db)
    before = sorted((c.id, c.is_current) for c in db.query(DishCost))
    assert menu_costs.recost(db, k.rid)["written"] == 0
    assert menu_costs.recost(db, k.rid, item_ids={k.beef.id})["written"] == 0
    db.commit()
    assert sorted((c.id, c.is_current) for c in db.query(DishCost)) == before and len(before) == 2


def test_a_new_invoice_price_recosts_the_dish_within_the_minute_and_leaves_one_current_row(client, db):
    k = Kitchen(client, db)
    old = k.current()
    started = time.monotonic()
    assert k.beef_at(14.00) == 1  # the commit itself queued one recost, for the items on that invoice
    assert time.monotonic() - started < 60
    new = k.current()
    assert new.id != old.id and new.plate_cost == D("2.7406")  # 150 g at $0.014
    assert db.get(DishCost, old.id).is_current is False
    assert db.query(DishCost).filter_by(recipe_id=k.burger.id, is_current=True).count() == 1
    assert db.query(DishCost).filter_by(recipe_id=k.sauce.id).count() == 1  # the sauce does not use beef: not recosted
    event = db.query(Event).filter_by(type="plate_cost.changed").order_by(Event.seq.desc()).first()
    assert event.payload == {"recipe_id": k.burger.id, "plate_cost": "2.7406", "before": "2.4406"}
    trigger = db.query(Event).filter_by(type="price_point.created").order_by(Event.seq.desc()).first()
    assert new.trigger_event_id == str(trigger.seq) and trigger.payload == {"ids": [k.beef.id]}
    # A move of less than a cent writes the row but is not announced.
    changed = db.query(Event).filter_by(type="plate_cost.changed").count()
    k.beef_at(14.01)
    assert k.current().plate_cost == D("2.7421") and db.query(Event).filter_by(type="plate_cost.changed").count() == changed


def test_a_price_change_in_a_sub_recipe_recosts_the_dishes_that_use_it_sub_recipe_first(client, db):
    k = Kitchen(client, db)
    _invoice(db, k.rid, k.farm, k.today - timedelta(days=1), [(k.base, 1, 9.00)])
    jobs.drain(db, queues=("derive",))
    db.commit()
    assert k.current(k.sauce).batch_cost == D("4.5000")
    assert k.current().plate_cost == D("2.5006")  # 20 g of a $4.50 batch is $0.18, up from $0.12
    assert k.current(k.sauce).effective_at <= k.current().effective_at


def test_the_alert_opens_one_finding_with_its_weekly_impact_and_a_capped_reprice_then_closes(client, db):
    k = Kitchen(client, db)
    k.beef_at(22.00)  # 150 g at $0.022: the plate is $3.9406, 32.8% of $12
    cost = k.current()
    assert (cost.plate_cost, cost.food_cost_pct) == (D("3.9406"), D("0.3284"))
    finding = k.finding()
    # (3.9406 - 2.4406) x 140 sold / 4
    assert finding.weekly_dollars == D("52.5000") and finding.title == "Burger: food cost 32.8% against a 30.0% target"
    assert finding.basis["plate_cost_28_days_ago"] == "2.4406" and finding.basis["units_sold_28_days"] == "140.0"
    proposal = db.get(AgentAction, finding.proposal_id)
    p = proposal.payload
    assert (proposal.action_type, proposal.status) == ("menu_reprice", "pending")
    # 3.9406 / 0.30 = 13.14, rounded up to $14.00; the cap is $13.20, rounded down to $13.00.
    assert (p["price_at_target"], p["price_cap"], p["suggested_price"], p["cap_applied"], p["food_cost_pct_at_suggested"]) == (
        "14.00", "13.00", "13.00", True, "0.3031")
    assert "$13.00 brings food cost to 30.3%" in p["note"] and "FullHouse never writes to a menu" in p["note"]
    assert (p["current_price"], p["suggested_delta"], p["recipe_id"]) == (12.0, 1.0, k.burger.id)  # what Recommendations already reads

    # Still over target after another rise: the same finding, refreshed; no second finding, no second proposal.
    k.beef_at(23.00)
    db.expire_all()
    assert db.query(Finding).filter_by(subject_id=k.burger.id).count() == 1 and k.finding().weekly_dollars == D("57.7500")
    assert db.query(AgentAction).filter_by(action_type="menu_reprice").count() == 1

    # Approving changes nothing in FullHouse: the price is changed in the POS by a person.
    assert client.post(f"/api/v1/agents/actions/{proposal.id}/approve").status_code == 200
    assert db.query(PosItem).filter_by(pos_item_id="BURGER").one().price == D("12.0000")

    # 31.3% (between target + 1 and target + 2 points) keeps the finding open; under 31% closes it.
    k.beef_at(20.80)
    assert k.current().food_cost_pct == D("0.3134") and k.finding() is not None
    k.beef_at(12.00)
    db.expire_all()
    assert k.finding() is None and k.finding("closed").note == "Food cost fell to 20.3%."


@pytest.mark.parametrize("case", ["few_sold", "unpriced_line", "no_price", "not_confirmed"])
def test_no_alert_without_enough_sales_a_full_cost_a_menu_price_or_a_confirmed_recipe(client, db, case):
    k = Kitchen(client, db, sold_a_day=0)
    if case == "few_sold":
        db.add(SalesItem(restaurant_id=k.rid, date=k.today - timedelta(days=2), pos_item_id="BURGER", pos_item_name="Burger", qty=9, net=108.0, source="square"))
    elif case != "no_price":
        db.add(SalesItem(restaurant_id=k.rid, date=k.today - timedelta(days=2), pos_item_id="BURGER", pos_item_name="Burger", qty=50, net=600.0, source="square"))
    if case == "unpriced_line":
        pickle = PriceItem(restaurant_id=k.rid, name="Pickle", unit="g", pack_unit="jar", pack_size=500)
        db.add(pickle)
        db.flush()
        db.add(RecipeLine(restaurant_id=k.rid, recipe_id=k.burger.id, position=9, item_id=pickle.id, qty=10, unit="g"))
    if case == "no_price":  # no price in the POS catalog and no sales to take one from
        db.query(PosItem).delete()
    if case == "not_confirmed":
        k.burger.status = PROPOSED
    db.commit()
    k.beef_at(30.00)
    assert k.finding() is None and db.query(AgentAction).filter_by(action_type="menu_reprice").count() == 0
    if case == "unpriced_line":
        cost = k.current()
        assert (cost.lines_priced, cost.lines_total, cost.unpriced) == (4, 5, ["Pickle (no invoice price)"])  # shown as "at least"
    if case == "no_price":
        cost = k.current()
        assert cost.menu_price is None and cost.food_cost_pct is None and cost.plate_cost == D("5.1406")  # the cost, with no percentage


def test_the_menu_price_falls_back_to_the_median_of_net_per_item_over_14_days(client, db):
    k = Kitchen(client, db)
    db.query(PosItem).delete()
    for back, net in ((1, 55.0), (2, 60.0), (3, 65.0)):  # 5 sold a day: $11, $12, $13 each
        db.query(SalesItem).filter_by(date=k.today - timedelta(days=back)).update({"net": net})
    for row in db.query(SalesItem).filter(SalesItem.date < k.today - timedelta(days=3)):
        row.net = 5 * 12.0
    db.commit()
    menu_costs.recost(db, k.rid)
    db.commit()
    cost = k.current()
    assert cost.menu_price == D("12.0000") and cost.basis["menu_price"]["source"] == "sales_items"


def test_a_fourth_reprice_in_a_week_is_held_back_and_logged(client, db):
    k = Kitchen(client, db)
    world = menu_costs.load(db, k.rid)
    for n in range(4):
        dish = Recipe(restaurant_id=k.rid, name=f"Dish {n}", pos_item_id=f"D{n}", yield_qty=1, yield_unit="plate", status=CONFIRMED)
        db.add(dish)
        db.flush()
        db.add(RecipeLine(restaurant_id=k.rid, recipe_id=dish.id, position=0, item_id=k.beef.id, qty=400, unit="g"))  # $4.80 on a $12 plate: 40%
        db.add(PosItem(restaurant_id=k.rid, pos_item_id=f"D{n}", name=dish.name, price=D("12.00")))
        db.add(SalesItem(restaurant_id=k.rid, date=k.today - timedelta(days=1), pos_item_id=f"D{n}", pos_item_name=dish.name, qty=40, net=480.0, source="square"))
    db.commit()
    jobs.drain(db, queues=("derive",))
    db.commit()
    assert db.query(Finding).filter_by(restaurant_id=k.rid, status="open").count() == 4  # every dish is still flagged
    assert db.query(AgentAction).filter_by(action_type="menu_reprice").count() == 3
    held = db.query(Event).filter_by(type="proposal.held").all()
    assert len(held) == 1 and held[0].payload["kind"] == "menu_reprice" and held[0].payload["cap"] == 3
    unproposed = db.query(Finding).filter_by(restaurant_id=k.rid, proposal_id=None).one()
    refused = client.post(f"{API}/menu-costs/{unproposed.subject_id}/reprice")
    assert refused.status_code == 409 and "three reprice suggestions" in refused.json()["detail"]
    del world


def test_an_outlier_price_is_not_used_for_costing_and_is_listed_for_review(client, db):
    k = Kitchen(client, db)
    assert k.beef_at(1200.00) == 1  # a case price typed as a unit price: 100 times the usual
    assert k.current().plate_cost == D("2.4406")  # unchanged
    out = menu_costs_read.detail(db, k.rid, k.burger.id)
    assert [(o["item"], o["unit_price"], o["median"]) for o in out["left_out"]] == [("Ground beef", "1.2", "0.012")]
    assert client.get(f"{API}/menu-costs").json()["left_out"] == 1
    k.beef_at(3.00, days_ago=0)  # and a price of a quarter of the usual
    assert k.current().plate_cost == D("2.4406")
    k.beef_at(30.00, days_ago=0)  # 2.5 times is a real rise: used
    assert k.current().plate_cost == D("5.1406")


def test_the_30_day_average_policy_weights_each_price_by_what_was_bought(client, db):
    k = Kitchen(client, db)
    db.add(RestaurantSettings(restaurant_id=k.rid, costing_price_policy="avg_30d"))
    db.commit()
    _invoice(db, k.rid, k.farm, k.today - timedelta(days=10), [(k.beef, 1, 12.00)])
    _invoice(db, k.rid, k.farm, k.today - timedelta(days=2), [(k.beef, 3, 16.00)])
    jobs.drain(db, queues=("derive",))
    db.commit()
    cost = k.current()
    # (1 kg at $12 + 3 kg at $16) / 4 kg = $15 a kg; the 40-day-old invoice is outside the window.
    assert cost.basis["lines"][0]["unit_price"] == "0.015" and cost.basis["policy"] == "avg_30d" and cost.plate_cost == D("2.8906")


def test_saving_a_recipe_recosts_it_and_a_cycle_is_rejected(client, db):
    k = Kitchen(client, db)
    lines = [{"item_id": k.beef.id, "qty": "150", "unit": "g"}, {"item_id": k.bun.id, "qty": "1", "unit": "each"},
             {"item_id": k.onion.id, "qty": "30", "unit": "g", "yield_pct": "0.75"}, {"sub_recipe_id": k.sauce.id, "qty": "20", "unit": "g"}]
    saved = client.put(f"{API}/recipes/{k.burger.id}", json={"lines": lines})
    assert saved.status_code == 200 and saved.json()["written"] == 1
    assert k.current().plate_cost == D("2.4500")  # onion at 75% yield: 40 g bought, $0.08
    assert db.query(DishCost).filter_by(recipe_id=k.burger.id, is_current=True).count() == 1
    jobs.drain(db, queues=("derive",))  # the queued recost for the same save finds nothing to write
    db.commit()
    assert db.query(DishCost).filter_by(recipe_id=k.burger.id).count() == 2

    cycle = client.put(f"{API}/recipes/{k.sauce.id}", json={"lines": [{"sub_recipe_id": k.burger.id, "qty": "1", "unit": "plate"}]})
    assert cycle.status_code == 422 and "use itself" in cycle.json()["detail"]
    itself = client.put(f"{API}/recipes/{k.burger.id}", json={"lines": [{"sub_recipe_id": k.burger.id, "qty": "1", "unit": "plate"}]})
    assert itself.status_code == 422
    assert db.query(RecipeLine).filter_by(recipe_id=k.sauce.id).count() == 1  # nothing was saved
    for bad in ({"lines": [{"item_id": k.beef.id, "sub_recipe_id": k.sauce.id, "qty": "1", "unit": "g"}]}, {"lines": [{"item_id": k.beef.id, "qty": "1", "unit": "g", "yield_pct": "0"}]},
                {"lines": [{"item_id": k.beef.id, "qty": "1", "unit": "g", "yield_pct": "1.2"}]}, {"lines": [{"item_id": "nope", "qty": "1", "unit": "g"}]}, {"lines": []}):
        assert client.put(f"{API}/recipes/{k.burger.id}", json=bad).status_code == 422

    # Eight portions a batch: the plate is an eighth of the batch.
    soup = client.put(f"{API}/recipes/{k.burger.id}", json={"portions": "8", "lines": [{"item_id": k.onion.id, "qty": "1000", "unit": "g"}]})
    assert soup.status_code == 200 and (k.current().batch_cost, k.current().plate_cost) == (D("2.0000"), D("0.2500"))


def test_the_list_and_dish_pages_read_what_was_computed(client, db):
    k = Kitchen(client, db)
    k.beef_at(22.00)
    out = client.get(f"{API}/menu-costs")
    body = out.json()
    assert (body["state"], body["stale"], body["target"]) == ("live", False, "0.3000")
    assert body["header"] == {"dishes_over": 1, "weekly_dollars": "52.5000", "dishes": 1}
    assert [s["done"] for s in body["setup"]] == [True, True, True, True]
    dish = body["dishes"][0]
    assert (dish["name"], dish["price"], dish["plate_cost"], dish["food_cost_pct"], dish["change_28"], dish["sold_28"], dish["partial"]) == (
        "Burger", "12.0000", "3.9406", "0.3284", "1.5000", "140.0", False)
    assert len(dish["trend"]) == 12 and dish["trend"][-1] == "3.9406" and dish["trend"][-2] == "2.4406" and dish["trend"][0] is None
    assert dish["finding"]["weekly_dollars"] == "52.5000" and dish["finding"]["suggested_price"] == "13.00" and dish["finding"]["proposal_status"] == "pending"
    tiles = body["tiles"]
    assert tiles["dishes_over"] == {"value": 1, "then": 0, "of": 1}
    assert tiles["average_food_cost"] == {"value": "0.3284", "then": "0.2034"}  # weighted by what sold
    assert (tiles["largest_increase"]["name"], tiles["largest_increase"]["pct"]) == ("Ground beef", "0.8333")
    assert "House sauce" not in [d["name"] for d in body["dishes"]]  # a sub-recipe is not a dish

    # Unchanged data answers 304 to the ETag it was sent with.
    assert client.get(f"{API}/menu-costs", headers={"If-None-Match": out.headers["etag"]}).status_code == 304

    detail = client.get(f"{API}/menu-costs/{k.burger.id}").json()
    assert detail["cost"]["plate_cost"] == "3.9406" and detail["cost"]["price_source"] == "Point of sale price" and detail["sold_28"] == "140.0"
    assert [(l["name"], l["line_cost"], l["change_28"]) for l in detail["lines"]] == [
        ("Ground beef", "3.3000", "1.5000"), ("Bun", "0.4500", "0.0000"), ("House sauce", "0.1200", "0.0000"), ("Onion", "0.0706", "0.0000")]
    assert detail["lines"][0]["supplier"] == "Farm"
    assert len(detail["history"]) == 26 and detail["history"][-1] == {"week": k.today.isoformat(), "plate_cost": "3.9406", "menu_price": "12.0000", "changed": ["Ground beef"]}
    assert detail["history"][0]["plate_cost"] is None  # before the first invoice: nothing to show, not a guess
    assert detail["finding"]["weekly_dollars"] == "52.5000" and detail["proposal"]["suggested_price"] == "13.00" and detail["proposal"]["cap_applied"] is True
    assert [l["yield_pct"] for l in detail["recipe"]["lines"]] == ["1", "1", "0.8500", "1"]
    assert client.get(f"{API}/menu-costs/nope").status_code == 404


def test_the_why_panel_reads_the_stored_basis_of_a_cost_and_of_a_finding(client, db):
    k = Kitchen(client, db)
    k.beef_at(22.00)
    cost = k.current()
    why = client.get(f"{API}/why/dish_costs/{cost.id}").json()
    assert why["result"] == {"value": "3.9406", "kind": "dollars", "label": "a plate"}
    assert why["formula"] == "Cost per plate = sum of each ingredient's quantity x its latest price"
    assert [(i["label"], i["value"]) for i in why["inputs"]] == [
        ("Ground beef", "150 g x $0.022 per g = $3.30"), ("Bun", "1 each x $0.45 per each = $0.45"),
        ("Onion", "30 g / 0.85 yield x $0.002 per g = $0.07"), ("House sauce", "20 g x $0.006 per g = $0.12")]
    assert why["inputs"][0]["source"]["kind"] == "invoice" and why["inputs"][3]["source"] == {"kind": "recipe", "id": k.sauce.id, "label": "Sub-recipe"}
    assert why["data_basis"] == "4 of 4 recipe lines priced, from 2 invoices" and why["computed_at"] and why["fix"] == {"label": "Edit recipe", "route": f"menu-costs/{k.burger.id}"}
    pct = client.get(f"{API}/why/dish_costs/{cost.id}?figure=food_cost_pct").json()
    assert pct["result"]["value"] == "0.3284" and [i["value"] for i in pct["inputs"]] == ["$3.94", "$12.00"]
    finding = client.get(f"{API}/why/findings/{k.finding().id}").json()
    assert finding["result"] == {"value": "52.5000", "kind": "dollars", "label": "a week"}
    assert [i["value"] for i in finding["inputs"]] == ["$3.94", "$2.44", "140"]
    # The panel shows what was stored: a later price does not change an old row's explanation.
    k.beef_at(24.00)
    assert client.get(f"{API}/why/dish_costs/{cost.id}").json()["inputs"][0]["value"] == "150 g x $0.022 per g = $3.30"
    assert client.get(f"{API}/why/dish_costs/nope").status_code == 404 and client.get(f"{API}/why/plate_costs/{cost.id}").status_code == 404


def test_setup_state_shows_what_is_missing_and_no_numbers(client, db):
    rid = _set_up(client, db)["id"]
    flags.set_flag(db, rid, flags.ML_MENU_COSTS, True)
    db.commit()
    body = client.get(f"{API}/menu-costs").json()
    assert body["state"] == "setup" and body["dishes"] == [] and body["header"] is None and body["tiles"] is None
    assert [(s["key"], s["done"]) for s in body["setup"]] == [("invoices", False), ("recipes", False), ("costed", False), ("prices", False)]
    assert client.get(f"{API}/digest-cards").json() == [{"module": "menu_costs", "state": "setup", "route": "menu-costs", "missing": ["invoices", "recipes", "costed", "prices"]}]


def test_the_digest_card_appears_only_while_a_dish_is_over_target(client, db):
    k = Kitchen(client, db)
    assert client.get(f"{API}/digest-cards").json() == []
    k.beef_at(22.00)
    card = client.get(f"{API}/digest-cards").json()
    assert len(card) == 1 and (card[0]["dishes_over"], card[0]["weekly_dollars"], card[0]["target"], card[0]["top"]["name"]) == (1, "52.5000", "0.3000", "Burger")


def test_a_recipe_confirmed_before_the_module_was_on_is_costed_on_first_read(client, db):
    k = Kitchen(client, db, flag=False)
    assert db.query(DishCost).count() == 0 and client.get(f"{API}/menu-costs").status_code == 404  # the module is off
    assert db.query(Event).filter(Event.type.in_(("price_point.created", "recipe.saved"))).count() == 0  # and nothing was queued for it
    flags.set_flag(db, k.rid, flags.ML_MENU_COSTS, True)
    db.commit()
    assert client.get(f"{API}/menu-costs").json()["dishes"][0]["plate_cost"] == "2.4406"


def test_another_restaurant_sees_none_of_it(client, db):
    k = Kitchen(client, db)
    k.beef_at(22.00)
    other, b = _second_restaurant(db)
    flags.set_flag(db, other.id, flags.ML_MENU_COSTS, True)
    db.commit()
    assert b.get(f"{API}/menu-costs").json()["state"] == "setup"
    assert b.get(f"{API}/menu-costs/{k.burger.id}").status_code == 404
    assert b.get(f"{API}/why/dish_costs/{k.current().id}").status_code == 404 and b.get(f"{API}/why/findings/{k.finding().id}").status_code == 404
    assert b.put(f"{API}/recipes/{k.burger.id}", json={"lines": [{"item_id": k.beef.id, "qty": "1", "unit": "g"}]}).status_code == 404
    assert b.post(f"{API}/menu-costs/{k.burger.id}/reprice").status_code == 404
    assert db.query(DishCost).filter_by(restaurant_id=other.id).count() == 0 and db.query(Finding).filter_by(restaurant_id=other.id).count() == 0


def test_the_per_category_target_overrides_the_restaurants(client, db):
    k = Kitchen(client, db)
    db.add(RestaurantSettings(restaurant_id=k.rid, overrides={"target_food_cost_pct_by_category": {"Mains": "0.18"}}))
    db.commit()
    menu_costs.recost(db, k.rid)
    db.commit()
    assert k.finding().title == "Burger: food cost 20.3% against a 18.0% target"  # 20.3% is at least 18% + 2 points
