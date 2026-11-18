"""Module E against the database (spec sections 9, 11 and 12): payout matching with the contract-rate fallback,
the per-unit rows, the finding rule and the capped `channel_reprice` proposal, and the routes the page reads."""

from datetime import timedelta
from decimal import Decimal as D

from app.agents.models import AgentAction
from app.margin_labor import channels, fixtures, menu_costs
from app.margin_labor import settings as ml_settings
from app.margin_labor.models import ChannelItemMargin, ChannelPayout, ChannelTerms, Finding, SalesOrder
from app.platform import flags, jobs
from app.platform.models import Event, Job
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/margin-labor"


class Kitchen:
    def __init__(self, client, db, terms=True):
        self.db, self.client = db, client
        self.rid = _set_up(client, db)["id"]
        for flag in (flags.ML_MENU_COSTS, flags.ML_CHANNELS):
            flags.set_flag(db, self.rid, flag, True)
        db.commit()
        self.today = menu_costs.load(db, self.rid).today
        made = fixtures.seed_kitchen(db, self.rid, self.today)
        self.recipes = made["recipes"]
        fixtures.seed_orders(db, self.rid, self.today, ml_settings.for_restaurant(db, self.rid).timezone, terms=terms)
        db.commit()
        jobs.drain(db, queues=("derive",))
        db.commit()
        self.run()

    def run(self):
        out = channels.run(self.db, self.rid)
        self.db.commit()
        self.db.expire_all()
        return out

    def row(self, pos, channel="delivery_platform", platform="DoorDash"):
        return self.db.query(ChannelItemMargin).filter_by(restaurant_id=self.rid, pos_item_id=pos, channel=channel, platform=platform,
                                                          window_end=self.today - timedelta(days=1)).one()


def test_rows_are_per_item_channel_and_platform_with_the_fees_each_unit_carried(client, db):
    k = Kitchen(client, db)
    burger = k.row("BURGER")
    # $13.00 of a $25.00 order: 52% of a $6.25 commission and a $0.75 fee. Two orders in three have a payout row and carry
    # only packaging ($0.70) as a fixed cost; the third takes the contract's $1.00.
    assert (burger.units, burger.unit_revenue, burger.commission_unit, burger.processing_unit, burger.fixed_unit) == (D("84.0000"), D("13.0000"), D("3.2500"), D("0.3900"), D("0.4160"))
    assert (burger.plate_cost, burger.unit_contribution, burger.contribution_pct, burger.payout_match_rate) == (D("2.4406"), D("6.5034"), D("0.500262"), D("0.666667"))
    assert burger.basis["orders"] == 84 and burger.basis["matched_orders"] == 56 and burger.basis["terms_orders"] == 28 and burger.window_start == k.today - timedelta(days=28)
    dine = k.row("BURGER", "dine_in", "")
    assert (dine.units, dine.unit_revenue, dine.commission_unit, dine.fixed_unit, dine.unit_contribution, dine.payout_match_rate) == (
        D("112.0000"), D("12.0000"), D("0.0000"), D("0.0000"), D("9.5594"), None)
    fries = k.row("FRIES", platform="Uber Eats")  # no payout rows at all: the contract rates, 30% and 3%, $2.00 and $1.00
    assert (fries.unit_revenue, fries.commission_unit, fries.processing_unit, fries.fixed_unit, fries.plate_cost, fries.unit_contribution, fries.payout_match_rate) == (
        D("5.0000"), D("1.5000"), D("0.1500"), D("3.0000"), D("0.4350"), D("-0.0850"), D("0.000000"))
    assert db.query(ChannelItemMargin).filter_by(restaurant_id=k.rid).count() == 5
    # Payout rows for the window's orders now point at the orders they matched; older ones are left alone.
    assert db.query(ChannelPayout).filter(ChannelPayout.order_id.isnot(None)).count() == 56 and db.query(ChannelPayout).count() == 70


def test_acceptance_an_unmatched_order_falls_back_to_the_contract_rates_and_the_page_shows_the_match_rate(client, db):
    k = Kitchen(client, db)
    body = client.get(f"{API}/channels").json()
    assert (body["state"], body["match_rate"], body["contract_rates"], body["platforms"]) == ("live", "0.500000", True, ["DoorDash", "Uber Eats"])  # under 80%: "Based on your contract rates"
    wings = next(i for i in body["items"] if i["name"] == "Wings")
    assert (wings["commission"], wings["processing"], wings["fixed"], wings["fees"], wings["match_rate"]) == ("3.0000", "0.3600", "0.3840", "3.7440", "0.666667")
    # With every DoorDash payout in, the fallback is no longer used there and the match rate says so.
    for day in (k.today - timedelta(days=n) for n in range(1, 36)):
        db.add(ChannelPayout(restaurant_id=k.rid, platform="DoorDash", external_order_id=f"DD-{day.isoformat()}-2", gross=D("25"), commission=D("5.00"), fees=D("0.75"), payout=D("19.25")))
    db.commit()
    assert [j.payload["job"] for j in db.query(Job).filter(Job.queue == "derive", Job.status == "queued")] == []  # payouts added outside an import queue nothing
    k.run()
    burger = k.row("BURGER")
    assert (burger.payout_match_rate, burger.fixed_unit, burger.commission_unit) == (D("1.000000"), D("0.3640"), D("3.0333"))  # the third order's actual $5.00, not 25%
    assert client.get(f"{API}/channels").json()["match_rate"] == "0.750000"  # Uber Eats still has none


def test_fries_lose_money_on_uber_eats_one_finding_with_a_reprice_that_reaches_the_target(client, db):
    k = Kitchen(client, db)
    finding = db.query(Finding).filter_by(restaurant_id=k.rid, type="channel_margin", status="open").one()
    # No fries sell dine-in, so the target is the dine-in price less the plate: 5.00 - 0.435. (4.565 + 0.085) x 28 / 4.
    assert (finding.subject_id, finding.weekly_dollars, finding.title) == ("FRIES|Uber Eats", D("32.5500"), "Fries keeps -$0.09 an item on Uber Eats")
    assert finding.basis["target_contribution"] == "4.5650" and finding.basis["row_id"] == k.row("FRIES", platform="Uber Eats").id
    proposal = db.get(AgentAction, finding.proposal_id)
    p = proposal.payload
    # (0.435 + 3.00 + 4.565) / (1 - 0.30 - 0.03) = 11.94, rounded up to $12.00, which keeps 12 x 0.67 - 3.435.
    assert (proposal.action_type, p["price"], p["suggested_price"], p["contribution_at_suggested"], p["rates_from"]) == ("channel_reprice", "11.94", "12.00", "4.61", "contract")
    assert p["check"] == "Check your agreement with the platform before changing prices." and p["check"] in p["note"]
    # Burger and wings keep well over half of what they keep dine-in: no finding.
    assert db.query(Finding).filter_by(restaurant_id=k.rid, type="channel_margin").count() == 1

    # Running again changes nothing; approving changes no price anywhere.
    rows = sorted((r.id, r.unit_contribution) for r in db.query(ChannelItemMargin))
    k.run()
    assert sorted((r.id, r.unit_contribution) for r in db.query(ChannelItemMargin)) == rows
    assert db.query(Finding).filter_by(type="channel_margin").count() == 1 and db.query(AgentAction).filter_by(action_type="channel_reprice").count() == 1
    approved = client.post(f"/api/v1/agents/actions/{proposal.id}/approve")
    assert approved.status_code == 200 and "Change it on the platform; FullHouse never does." in approved.json()["applied_result"]
    assert k.row("FRIES", platform="Uber Eats").unit_revenue == D("5.0000")
    explained = client.get("/api/v1/agents/actions").json()
    assert any("Uber Eats" in str(a) for a in explained)

    # Cheaper rates on Uber Eats from the start of the window: the fries keep enough, and the finding closes.
    done = client.put(f"{API}/channels/terms", json={"platform": "Uber Eats", "commission_pct": "0.10", "processing_pct": "0.03", "fixed_fee_per_order": "0", "packaging_cost_per_order": "0.20",
                                                    "effective_from": "2000-01-01"})
    assert done.status_code == 200
    db.expire_all()
    assert k.row("FRIES", platform="Uber Eats").unit_contribution == D("3.7150")  # 5 - 0.50 - 0.15 - 0.20 - 0.435
    assert db.query(Finding).filter_by(type="channel_margin", status="open").count() == 0 and db.query(Finding).filter_by(type="channel_margin", status="closed").count() == 1


def test_an_item_keeping_under_half_of_its_dine_in_contribution_is_flagged_and_few_units_are_not(client, db):
    k = Kitchen(client, db)
    # DoorDash at 55%, with no payout rows: a wings keeps 12 - 6.60 - 0.36 - 0.48 - 2.80 = 1.76, under half of the 8.20 it keeps dine-in.
    client.put(f"{API}/channels/terms", json={"platform": "DoorDash", "commission_pct": "0.55", "processing_pct": "0.03", "fixed_fee_per_order": "0.30",
                                              "packaging_cost_per_order": "0.70", "effective_from": "2000-01-01"})
    db.query(ChannelPayout).delete()
    db.commit()
    k.run()
    wings = k.row("WINGS")
    assert wings.unit_contribution == D("1.7600") and k.row("WINGS", "dine_in", "").unit_contribution == D("8.2000")
    assert k.row("BURGER").unit_contribution == D("2.4994")
    subjects = {f.subject_id for f in db.query(Finding).filter_by(restaurant_id=k.rid, type="channel_margin", status="open")}
    assert "WINGS|DoorDash" in subjects and "BURGER|DoorDash" in subjects  # the burger's 2.4994 is under half of 9.5594
    # Under ten sold on the platform in the window: no finding, whatever it keeps.
    keep = [o.id for o in db.query(SalesOrder).filter(SalesOrder.platform == "Uber Eats").order_by(SalesOrder.business_date.desc()).limit(9)]
    for order in db.query(SalesOrder).filter(SalesOrder.platform == "Uber Eats", SalesOrder.id.notin_(keep)):
        order.status = "voided"
    db.commit()
    k.run()
    assert k.row("FRIES", platform="Uber Eats").units == D("9.0000")
    assert "FRIES|Uber Eats" not in {f.subject_id for f in db.query(Finding).filter_by(restaurant_id=k.rid, type="channel_margin", status="open")}


def test_a_fourth_platform_price_in_a_week_is_held_back_and_logged(client, db):
    k = Kitchen(client, db)
    client.put(f"{API}/channels/terms", json={"platform": "DoorDash", "commission_pct": "0.80", "processing_pct": "0.03", "effective_from": "2000-01-01"})
    db.query(ChannelPayout).delete()
    db.commit()
    from app.margin_labor.models import SalesOrderLine

    # Two more items on every DoorDash order, so four items lose money there.
    for order in db.query(SalesOrder).filter(SalesOrder.platform == "DoorDash").all():
        db.add(SalesOrderLine(restaurant_id=k.rid, order_id=order.id, line_no=3, business_date=order.business_date, pos_item_id="SALAD", pos_item_name="House Salad", qty=D(1), net=D("9.00")))
        db.add(SalesOrderLine(restaurant_id=k.rid, order_id=order.id, line_no=4, business_date=order.business_date, pos_item_id="FRIES", pos_item_name="Fries", qty=D(1), net=D("5.00")))
        order.net = D("39.00")
    db.commit()
    k.run()
    open_ = db.query(Finding).filter_by(restaurant_id=k.rid, type="channel_margin", status="open").count()
    assert open_ == 5  # four on DoorDash and the fries on Uber Eats: every one is still shown
    assert db.query(AgentAction).filter_by(action_type="channel_reprice").count() == 3
    held = [e for e in db.query(Event).filter_by(type="proposal.held") if e.payload["kind"] == "channel_reprice"]
    assert len(held) == 2 and held[0].payload["cap"] == 3
    unproposed = db.query(Finding).filter_by(restaurant_id=k.rid, type="channel_margin", proposal_id=None).first()
    refused = client.post(f"{API}/channels/{unproposed.basis['row_id']}/reprice")
    assert refused.status_code == 409 and "three platform prices" in refused.json()["detail"]


def test_setup_until_module_a_is_live_orders_carry_a_channel_and_every_platform_has_rates_or_payouts(client, db):
    k = Kitchen(client, db, terms=False)
    body = client.get(f"{API}/channels").json()
    # DoorDash has payout rows; Uber Eats has neither a payout nor rates.
    assert body["state"] == "setup" and body["items"] == [] and body["header"] is None and body["tiles"] == []
    assert [(s["key"], s["done"]) for s in body["setup"]] == [("menu_costs", True), ("orders", True), ("rates", False)] and body["setup"][2]["missing"] == ["Uber Eats"]
    assert db.query(Finding).filter_by(type="channel_margin").count() == 0
    assert [c for c in client.get(f"{API}/digest-cards").json() if c["module"] == "channels"] == [{"module": "channels", "state": "setup", "route": "channels", "missing": ["rates"]}]
    saved = client.put(f"{API}/channels/terms", json={"platform": "Uber Eats", "commission_pct": "0.30", "processing_pct": "0.03", "fixed_fee_per_order": "2.00", "packaging_cost_per_order": "1.00"})
    assert saved.status_code == 200 and saved.json()["terms"][0]["effective_from"] == "2000-01-01"  # the first rates given cover the platform's history
    body = client.get(f"{API}/channels").json()
    assert body["state"] == "live" and body["header"]["items"] == 1
    # DoorDash, with payouts and no contract: the unmatched third order has no fees to use, and the row says so.
    assert k.row("BURGER").basis["missing_fee_orders"] == 28 and k.row("BURGER").commission_unit == D("2.1667")
    assert client.put(f"{API}/channels/terms", json={"platform": "X", "commission_pct": "0.80", "processing_pct": "0.30"}).status_code == 422


def test_new_rates_start_today_and_the_old_ones_still_price_the_orders_they_applied_to(client, db):
    k = Kitchen(client, db)
    client.put(f"{API}/channels/terms", json={"platform": "Uber Eats", "commission_pct": "0.10", "processing_pct": "0.03"})
    terms = db.query(ChannelTerms).filter_by(restaurant_id=k.rid, platform="Uber Eats").order_by(ChannelTerms.effective_from).all()
    assert [(t.effective_from, t.effective_to, t.commission_pct) for t in terms] == [
        (terms[0].effective_from, k.today - timedelta(days=1), D("0.300000")), (k.today, None, D("0.100000"))]
    assert k.row("FRIES", platform="Uber Eats").unit_contribution == D("-0.0850")  # the window's orders were under the old rates


def test_the_page_tiles_chart_and_why_panel_read_the_rows(client, db):
    k = Kitchen(client, db)
    out = client.get(f"{API}/channels")
    body = out.json()
    assert body["header"] == {"items": 1, "weekly_dollars": "32.5500", "platform": "Uber Eats", "delivery_items": 3}
    assert [(t["label"], t["kept_pct"], t["units"]) for t in body["tiles"]] == [("Dine in", "0.780537", "168.0000"), ("DoorDash", "0.478376", "168.0000"), ("Uber Eats", "-0.017000", "28.0000")]
    assert [(i["name"], i["platform"], i["price"], i["plate_cost"], i["kept"], i["units"]) for i in body["items"]] == [
        ("Fries", "Uber Eats", "5.0000", "0.4350", "-0.0850", "28.0000"), ("Wings", "DoorDash", "12.0000", "2.8000", "5.4560", "84.0000"),
        ("Burger", "DoorDash", "13.0000", "2.4406", "6.5034", "84.0000")]
    fries = body["items"][0]
    assert fries["finding"]["weekly_dollars"] == "32.5500" and fries["finding"]["suggested_price"] == "12.00" and fries["finding"]["kept_at_suggested"] == "4.61"
    assert body["chart"]["series"] == ["Dine in", "DoorDash", "Uber Eats"]
    assert body["chart"]["items"][0] == {"pos_item_id": "BURGER", "name": "Burger", "values": {"Dine in": "9.5594", "DoorDash": "6.5034"}}
    assert [t["platform"] for t in body["terms"]] == ["DoorDash", "Uber Eats"]
    assert client.get(f"{API}/channels", headers={"If-None-Match": out.headers["etag"]}).status_code == 304

    why = client.get(f"{API}/why/channel_item_margins/{fries['row_id']}").json()
    assert why["result"] == {"value": "-0.0850", "kind": "dollars", "label": "kept per item on Uber Eats"}
    assert why["formula"] == "Kept per item = price - commission - processing - fixed fees and packaging - cost per plate"
    assert [(i["label"], i["value"]) for i in why["inputs"]] == [("Price", "$5.00"), ("Commission", "$1.50"), ("Processing", "$0.15"), ("Fixed fees and packaging", "$3.00"), ("Cost per plate", "$0.44")]
    assert why["inputs"][1]["source"]["label"] == "Contract rates" and "28 orders" in why["data_basis"] and "0 matched a payout report" in why["data_basis"]
    assert why["waterfall"]["start"] == {"label": "Price", "amount": "5.0000"} and why["waterfall"]["end"] == {"label": "Kept per item", "amount": "-0.0850"}
    assert [s["amount"] for s in why["waterfall"]["steps"]] == ["1.5000", "0.1500", "3.0000", "0.4350"]
    assert client.get(f"{API}/why/findings/{fries['finding']['id']}").json()["inputs"] == why["inputs"]
    card = [c for c in client.get(f"{API}/digest-cards").json() if c["module"] == "channels"][0]
    assert (card["items"], card["platform"], card["weekly_dollars"], card["top"]["name"]) == (1, "Uber Eats", "32.5500", "Fries")


def test_a_payout_import_or_a_cost_change_queues_the_job_and_another_restaurant_sees_nothing(client, db):
    k = Kitchen(client, db)
    text = "Platform,Order ID,Subtotal,Commission,Processing Fee,Net Payout\n" + "".join(
        f"Uber Eats,UE-{(k.today - timedelta(days=n)).isoformat()},5.00,-1.00,-0.15,3.85\n" for n in range(1, 29))
    assert client.post(f"{API}/data/import", json={"kind": "payouts", "text": text}).json()["created"] == 28
    db.expire_all()
    fries = k.row("FRIES", platform="Uber Eats")  # the import ran the job: actual fees, and only packaging as a fixed cost
    assert (fries.payout_match_rate, fries.commission_unit, fries.fixed_unit, fries.unit_contribution) == (D("1.000000"), D("1.0000"), D("1.0000"), D("2.4150"))
    assert db.query(Finding).filter_by(type="channel_margin", status="open").count() == 0  # 2.415 is over half of 4.565

    other, b = _second_restaurant(db)
    assert b.get(f"{API}/channels").status_code == 404
    flags.set_flag(db, other.id, flags.ML_CHANNELS, True)
    db.commit()
    theirs = b.get(f"{API}/channels").json()
    assert theirs["state"] == "setup" and theirs["items"] == [] and theirs["terms"] == []
    assert b.get(f"{API}/why/channel_item_margins/{fries.id}").status_code == 404 and b.post(f"{API}/channels/{fries.id}/reprice").status_code == 404
    assert db.query(ChannelItemMargin).filter_by(restaurant_id=other.id).count() == 0 and db.query(ChannelTerms).filter_by(restaurant_id=other.id).count() == 0
