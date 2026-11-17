"""Module B against the database (spec sections 6, 11 and 12): the windows, the coverage states, the two-week
rule, the data-gap rule, and the routes the pages read. The fixture kitchen buys 40 kg of wings a week and uses 28."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.margin_labor import fixtures, menu_costs, usage
from app.margin_labor.models import Finding, SalesOrder, SalesOrderLine, UsageVariance
from app.platform import flags, jobs
from app.platform.models import Job
from app.prices.models import Invoice, InvoiceLine
from app.sales.models import SalesItem
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/margin-labor"


class Kitchen:
    def __init__(self, client, db, extra_weeks=None, soda_net=None):
        self.db, self.client = db, client
        self.rid = _set_up(client, db)["id"]
        for flag in (flags.ML_MENU_COSTS, flags.ML_USAGE):
            flags.set_flag(db, self.rid, flag, True)
        db.commit()
        self.today = menu_costs.load(db, self.rid).today
        self.end = usage.window_end(self.today)
        made = fixtures.seed_kitchen(db, self.rid, self.today, deliveries=False)
        self.items, self.recipes, self.supplier = made["items"], made["recipes"], made["supplier"]
        fixtures.seed_deliveries(db, self.rid, self.supplier, self.items, self.end, extra_weeks=extra_weeks)
        if soda_net:  # a drink with no recipe
            for back in range(1, 91):
                db.add(SalesItem(restaurant_id=self.rid, date=self.today - timedelta(days=back), pos_item_id="SODA", pos_item_name="Soda", qty=40, net=soda_net, source="pos"))
        db.commit()
        jobs.drain(db, queues=("derive",))
        db.commit()

    def row(self, name, weeks_back=0):
        self.db.expire_all()
        return self.db.query(UsageVariance).filter_by(restaurant_id=self.rid, canonical_item_id=self.items[name].id, window_end=self.end - timedelta(days=7 * weeks_back)).one()

    def findings(self, status="open"):
        self.db.expire_all()
        return {(f.type, f.title.split(":")[0]) for f in self.db.query(Finding).filter(Finding.restaurant_id == self.rid, Finding.type.like("usage%"), Finding.status == status)}

    def run(self):
        out = usage.run(self.db, self.rid)
        self.db.commit()
        return out


def test_the_kitchen_buys_more_wings_than_its_recipes_use_and_that_is_the_one_finding(client, db):
    k = Kitchen(client, db)
    assert k.end.weekday() == 6 and k.end < k.today
    wings = k.row("Chicken wings")
    # 10 sold a day x 400 g x 28 days; eight 20 kg deliveries; the median delivery is the slack.
    assert (wings.window_start, wings.theoretical_qty, wings.purchased_qty, wings.noise_qty, wings.unexplained_qty) == (
        k.end - timedelta(days=27), D("112000.0000"), D("160000.0000"), D("20000.0000"), D("28000.0000"))
    assert (wings.avg_unit_price, wings.weekly_dollars, wings.recipe_coverage, wings.state, wings.calc_version) == (D("0.00700000"), D("49.0000"), D("1.000000"), "live", "b1")
    assert wings.basis["top_dish"]["name"] == "Wings" and wings.basis["deliveries_in_window"] == 8 and wings.basis["last_delivery"]["supplier"] == "Margin Farm"
    beef = k.row("Ground beef")
    assert (beef.theoretical_qty, beef.purchased_qty, beef.unexplained_qty) == (D("126000.0000"), D("128000.0000"), D("-30000.0000"))  # inside one delivery
    onion = k.row("Onion")  # 30 g at 85% yield in 30 burgers a day, and 20 g in 14 salads
    assert onion.theoretical_qty == D("37487.0588") and [d["name"] for d in onion.basis["dishes"]] == ["Burger", "House Salad"]
    assert k.row("Sauce base").theoretical_qty == D("16800.0000")  # through the house sauce
    assert k.findings() == {("usage_variance", "Chicken wings")}
    finding = db.query(Finding).filter_by(restaurant_id=k.rid, type="usage_variance").one()
    assert finding.weekly_dollars == D("49.0000") and finding.title == "Chicken wings: $49.00 a week bought beyond what recipes used"
    assert finding.basis["checks"]["top_dish"]["name"] == "Wings" and finding.basis["row_id"] == wings.id
    assert db.query(UsageVariance).filter_by(restaurant_id=k.rid).count() == 8 * 8  # eight ingredients, eight weekly windows


def test_running_the_job_twice_leaves_the_same_rows_and_findings(client, db):
    k = Kitchen(client, db)
    before = sorted((r.id, r.window_end, r.weekly_dollars, r.unexplained_qty) for r in db.query(UsageVariance))
    ids = sorted(f.id for f in db.query(Finding))
    assert k.run() == {"window_end": k.end.isoformat(), "items": 8, "state": "live", "open_findings": 1}
    k.run()
    db.expire_all()
    assert sorted((r.id, r.window_end, r.weekly_dollars, r.unexplained_qty) for r in db.query(UsageVariance)) == before
    assert sorted(f.id for f in db.query(Finding)) == ids


def test_a_gap_seen_on_one_weekly_run_only_opens_no_finding_yet(client, db):
    k = Kitchen(client, db, extra_weeks=4)  # the Monday wings began four weeks ago
    assert k.row("Chicken wings").weekly_dollars == D("49.0000")
    assert k.row("Chicken wings", weeks_back=1).weekly_dollars == D("14.0000")  # the week before: 8 kg over, under $25
    assert k.findings() == set()
    body = client.get(f"{API}/usage").json()  # the page still shows it; only the finding waits
    assert body["header"] == {"weekly_dollars": "49.0000", "items": 1} and body["items"][0]["finding"] is None and body["tiles"]["flagged"] == {"value": 0, "then": 0}


def test_acceptance_at_85_percent_coverage_the_page_is_learning_and_opens_no_findings(client, db):
    k = Kitchen(client, db, soda_net=126.35)  # $716 a day has recipes, of $842.35
    wings = k.row("Chicken wings")
    assert (wings.recipe_coverage, wings.state, wings.weekly_dollars) == (D("0.850003"), "learning", D("49.0000"))
    assert db.query(Finding).filter(Finding.type.like("usage%")).count() == 0
    body = client.get(f"{API}/usage").json()
    assert (body["state"], body["coverage"], body["coverage_live"]) == ("learning", "0.850003", "0.90")
    assert body["items"][0]["name"] == "Chicken wings" and body["items"][0]["weekly_dollars"] == "49.0000"  # the table, as an estimate
    assert [(m["name"], m["has_recipe"]) for m in body["missing_recipes"]] == [("Soda", False)] and body["recipes_needed"] == 1
    assert client.get(f"{API}/digest-cards").json() == [] or all(c["module"] != "usage" for c in client.get(f"{API}/digest-cards").json())


def test_below_80_percent_coverage_the_page_is_setup_with_no_variance_numbers(client, db):
    k = Kitchen(client, db, soda_net=300.0)
    assert k.row("Chicken wings").state == "setup"
    body = client.get(f"{API}/usage").json()
    assert (body["state"], body["items"], body["header"], body["tiles"]) == ("setup", [], None, None)
    assert body["coverage"] == "0.704724" and body["missing_recipes"][0]["name"] == "Soda" and body["recipes_needed"] == 1
    detail = client.get(f"{API}/usage/{k.items['Chicken wings'].id}").json()
    assert (detail["state"], detail["current"], detail["windows"], detail["dishes"]) == ("setup", None, [], [])
    card = [c for c in client.get(f"{API}/digest-cards").json() if c["module"] == "usage"]
    assert card == [{"module": "usage", "state": "setup", "route": "usage", "recipes_needed": 1, "has_sales": True}]


def test_the_data_gap_rule_opens_its_own_finding_when_too_little_was_bought(client, db):
    k = Kitchen(client, db)
    db.query(InvoiceLine).filter(InvoiceLine.item_id == k.items["Lettuce"].id, InvoiceLine.qty == 17).delete()  # the lettuce is bought for cash
    db.commit()
    k.run()
    lettuce = k.row("Lettuce")
    assert lettuce.purchased_qty == D("0.0000") and lettuce.theoretical_qty == D("65333.3333")
    assert k.findings() == {("usage_variance", "Chicken wings"), ("usage_data_gap", "Lettuce")}
    gap = db.query(Finding).filter_by(restaurant_id=k.rid, type="usage_data_gap").one()
    assert gap.weekly_dollars == D("0.0000") and gap.title == "Lettuce: recipes used more than was bought, last 28 days"


def test_voids_after_fire_count_credits_subtract_and_outlier_lines_are_left_out(client, db):
    k = Kitchen(client, db)
    day = k.end - timedelta(days=2)
    order = SalesOrder(restaurant_id=k.rid, external_id="V-1", opened_at=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=23),
                       business_date=day, status="voided")
    db.add(order)
    db.flush()
    db.add(SalesOrderLine(restaurant_id=k.rid, order_id=order.id, line_no=1, business_date=day, pos_item_id="WINGS", pos_item_name="Wings", qty=D(5), net=D(0),
                          voided=True, voided_after_fire=True))
    db.add(SalesOrderLine(restaurant_id=k.rid, order_id=order.id, line_no=2, business_date=day, pos_item_id="WINGS", pos_item_name="Wings", qty=D(3), net=D(0), voided=True))
    line = db.query(InvoiceLine).join(Invoice).filter(InvoiceLine.item_id == k.items["Chicken wings"].id, Invoice.invoice_date == k.end - timedelta(days=3)).one()
    line.credited = 35.0  # $35 back at $0.007 a g: 5 kg returned
    bad = Invoice(restaurant_id=k.rid, supplier_id=k.supplier.id, invoice_date=k.end - timedelta(days=1), status="complete")
    db.add(bad)
    db.flush()
    db.add(InvoiceLine(restaurant_id=k.rid, invoice_id=bad.id, supplier_id=k.supplier.id, line_no=1, description="Chicken wings", qty=50, unit_price=700.0, extended=35000.0,
                       item_id=k.items["Chicken wings"].id, pack_size=1000))  # 100 times the usual price: a reading error
    db.commit()
    k.run()
    wings = k.row("Chicken wings")
    assert wings.theoretical_qty == D("114000.0000")  # five made and voided used the food; three voided before the kitchen did not
    assert wings.purchased_qty == D("155000.0000") and wings.basis["credits_qty"] == "5000.0000" and wings.basis["prices_left_out"] == 1
    assert wings.weekly_dollars == D("36.7500")  # (155,000 - 114,000 - 20,000) x $0.007 / 4


def test_an_untracked_ingredient_is_left_out_and_a_closed_gap_closes_its_finding(client, db):
    k = Kitchen(client, db)
    wings = k.items["Chicken wings"].id
    off = client.put(f"{API}/usage/{wings}/tracking", json={"track_usage": False})
    assert off.status_code == 200 and off.json()["items"] == 7
    assert db.query(UsageVariance).filter_by(canonical_item_id=wings).count() == 0
    assert k.findings() == set() and k.findings("closed") == {("usage_variance", "Chicken wings")}
    assert client.get(f"{API}/usage").json()["header"] == {"weekly_dollars": "0", "items": 0}
    client.put(f"{API}/usage/{wings}/tracking", json={"track_usage": True})
    assert k.findings() == {("usage_variance", "Chicken wings")}  # true again: a new finding

    # Dismissed: set aside while it stays true, not reopened by the next run.
    finding = db.query(Finding).filter_by(restaurant_id=k.rid, type="usage_variance", status="open").one()
    assert client.post(f"{API}/findings/{finding.id}/dismiss").json() == {"id": finding.id, "status": "dismissed"}
    k.run()
    assert k.findings() == set() and k.findings("dismissed") == {("usage_variance", "Chicken wings")}
    assert client.get(f"{API}/usage").json()["tiles"]["flagged"]["value"] == 0


def test_the_list_and_ingredient_pages_read_the_computed_windows(client, db):
    k = Kitchen(client, db)
    out = client.get(f"{API}/usage")
    body = out.json()
    assert (body["state"], body["stale"], body["coverage"], body["window_end"]) == ("live", False, "1.000000", k.end.isoformat())
    assert body["header"] == {"weekly_dollars": "49.0000", "items": 1}
    # The week before also holds the kitchen's first small invoice on some weekdays: 2 kg more, $52.50.
    assert body["tiles"]["unexplained"]["value"] == "49.0000" and body["tiles"]["unexplained"]["then"] in ("49.0000", "52.5000")
    assert body["tiles"]["coverage"] == {"value": "1.000000", "then": "1.000000", "needed": "0.90"} and body["tiles"]["flagged"] == {"value": 1, "then": 1}
    assert len(body["items"]) == 1 and body["more_under"] is None  # the seven with no gap are not rows
    wings = body["items"][0]
    assert (wings["name"], wings["base_unit"], wings["expected"], wings["purchased"], wings["unexplained"], wings["pct"], wings["weekly_dollars"]) == (
        "Chicken wings", "g", "112000.0000", "160000.0000", "28000.0000", "0.250000", "49.0000")
    assert len(wings["trend"]) == 8 and wings["trend"][-1] == "49.0000" and wings["finding"]["type"] == "usage_variance"
    assert wings["finding"]["checks"]["last_delivery"]["day"] == (k.end - timedelta(days=3)).isoformat()
    assert client.get(f"{API}/usage", headers={"If-None-Match": out.headers["etag"]}).status_code == 304

    detail = client.get(f"{API}/usage/{wings['item_id']}").json()
    assert detail["item"] == {"id": wings["item_id"], "name": "Chicken wings", "base_unit": "g", "track_usage": True}
    assert detail["current"]["noise"] == "20000.0000" and detail["current"]["avg_unit_price"] == "0.007" and detail["current"]["deliveries"] == 8
    assert len(detail["windows"]) == 8 and detail["windows"][-1]["window_end"] == k.end.isoformat() and detail["windows"][-1]["unexplained"] == "28000.0000"
    assert [(d["name"], d["sold"], d["expected"]) for d in detail["dishes"]] == [("Wings", "280.0", "112000.0000")]
    assert detail["finding"]["weekly_dollars"] == "49.0000" and detail["checks"]["top_dish"]["name"] == "Wings"
    assert client.get(f"{API}/usage/nope").status_code == 404

    why = client.get(f"{API}/why/usage_variance/{wings['row_id']}").json()
    assert why["result"] == {"value": "49.0000", "kind": "dollars", "label": "a week"}
    assert [(i["label"], i["value"]) for i in why["inputs"]] == [("Expected use", "112,000 g"), ("Purchased", "160,000 g"), ("One typical delivery", "20,000 g"),
                                                                 ("Average price", "$0.007 per g")]
    assert why["inputs"][0]["source"]["label"] == "Wings 280 sold x 400 g" and "recipe coverage 100.0%" in why["data_basis"]
    assert why["fix"] == {"label": "Review recipe", "route": f"menu-costs/{k.recipes['Wings'].id}"}
    assert client.get(f"{API}/why/findings/{wings['finding']['id']}").json()["inputs"] == why["inputs"]  # a finding explains itself by its row
    card = [c for c in client.get(f"{API}/digest-cards").json() if c["module"] == "usage"][0]
    assert (card["weekly_dollars"], card["items"], card["top"]["name"]) == ("49.0000", 1, "Chicken wings")


def test_small_gaps_are_collapsed_into_one_line(client, db):
    k = Kitchen(client, db)
    extra = Invoice(restaurant_id=k.rid, supplier_id=k.supplier.id, invoice_date=k.end - timedelta(days=5), status="complete")
    db.add(extra)
    db.flush()
    db.add(InvoiceLine(restaurant_id=k.rid, invoice_id=extra.id, supplier_id=k.supplier.id, line_no=1, description="Sauce base", qty=5, unit_price=6.0, extended=30.0,
                       item_id=k.items["Sauce base"].id, pack_size=1000))  # 25 kg bought against 16.8 used: 3.2 kg over, $4.80 a week
    db.commit()
    jobs.drain(db, queues=("derive",))
    db.commit()
    assert k.row("Sauce base").weekly_dollars == D("4.8000")
    body = client.get(f"{API}/usage").json()
    assert [i["name"] for i in body["items"]] == ["Chicken wings"] and body["more_under"] == {"count": 1, "threshold": "5"}


def test_a_new_invoice_or_recipe_queues_one_usage_job_and_another_restaurant_sees_nothing(client, db):
    k = Kitchen(client, db)
    db.add(Invoice(restaurant_id=k.rid, supplier_id=k.supplier.id, invoice_date=k.end, status="complete"))
    db.flush()
    invoice = db.query(Invoice).filter_by(invoice_date=k.end, restaurant_id=k.rid).order_by(Invoice.created_at.desc()).first()
    db.add(InvoiceLine(restaurant_id=k.rid, invoice_id=invoice.id, supplier_id=k.supplier.id, line_no=1, description="Lettuce", qty=1, unit_price=4.0, extended=4.0,
                       item_id=k.items["Lettuce"].id, pack_size=1000))
    db.commit()
    assert [j.payload["job"] for j in db.query(Job).filter(Job.queue == "derive", Job.status == "queued")] == ["recost", "usage_variance"]
    jobs.drain(db, queues=("derive",))
    db.commit()
    assert k.row("Lettuce").purchased_qty == D("69000.0000")

    other, b = _second_restaurant(db)
    assert b.get(f"{API}/usage").status_code == 404  # the module is off there
    flags.set_flag(db, other.id, flags.ML_USAGE, True)
    db.commit()
    assert b.get(f"{API}/usage").json()["state"] == "setup" and b.get(f"{API}/usage").json()["has_sales"] is False
    wings = k.row("Chicken wings")
    finding = db.query(Finding).filter_by(restaurant_id=k.rid, type="usage_variance").one()
    assert b.get(f"{API}/usage/{k.items['Chicken wings'].id}").status_code == 404
    assert b.get(f"{API}/why/usage_variance/{wings.id}").status_code == 404 and b.post(f"{API}/findings/{finding.id}/dismiss").status_code == 404
    assert b.put(f"{API}/usage/{k.items['Chicken wings'].id}/tracking", json={"track_usage": False}).status_code == 404
    assert db.query(UsageVariance).filter_by(restaurant_id=other.id).count() == 0
