"""Phase 5: checking deliveries in, what suppliers say back, and orders that are late."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.notify.models import Notification
from app.platform import flags
from app.prices import demo as price_demo
from app.prices import stock as price_stock
from app.prices.models import OutreachDraft, PriceItem
from app.receiving import engine
from app.restaurant import orders
from app.restaurant.models import InventoryAdjustment, InventoryItem, PurchaseOrder, Supplier
from app.stock import ledger
from test_restaurant_details import _second_restaurant
from test_restaurant_setup import _set_up

API = "/api/v1/receiving"
TODAY = date.today()


def _kitchen(client, db):
    """Eight weeks of invoices, tomatoes and romaine counted in stock, and an order of both placed with the farm."""
    restaurant = _set_up(client, db)
    rid = restaurant["id"]
    for flag in (flags.V3_RECEIVING, flags.V3_STOCK, flags.V2_ORDERS):
        flags.set_flag(db, rid, flag, True)
    suppliers = {}
    for name in price_demo.SUPPLIERS:
        supplier = Supplier(restaurant_id=rid, name=name, lead_time_days=1)
        db.add(supplier)
        db.flush()
        suppliers[name] = supplier
    tomatoes = InventoryItem(restaurant_id=rid, name="Tomatoes", unit="lb", quantity_on_hand=20, unit_cost=1.2, supplier_id=suppliers["Green Valley Farms"].id)
    romaine = InventoryItem(restaurant_id=rid, name="Romaine Lettuce", unit="head", quantity_on_hand=10, unit_cost=1.1)
    db.add_all([tomatoes, romaine])
    db.flush()
    price_demo.seed(db, rid, {name: s.id for name, s in suppliers.items()}, TODAY)
    items = {i.name: i for i in db.query(PriceItem).filter(PriceItem.restaurant_id == rid)}
    farm = suppliers["Green Valley Farms"]
    order = orders.draft_with_lines(db, rid, farm, [
        {"item_id": items["Tomatoes, 25 lb case"].id, "inventory_item_id": tomatoes.id, "description": "Tomatoes, 25 lb case", "qty": 4, "unit": "case",
         "units": 100, "unit_price_expected": 31.2},
        {"item_id": items["Romaine, 24 ct case"].id, "inventory_item_id": romaine.id, "description": "Romaine, 24 ct case", "qty": 2, "unit": "case",
         "units": 48, "unit_price_expected": 28.1},
    ], today=TODAY)
    orders.mark_sent(db, order, sent_by="owner@example.com")
    db.commit()
    return rid, order, tomatoes, romaine, farm


def _lines(client):
    return {l["description"]: l for l in client.get(f"{API}/expected").json()[0]["lines"]}


def test_acceptance_a_short_delivery_is_booked_once_closes_part_received_and_drafts_a_credit_claim(client, db):
    rid, order, tomatoes, romaine, farm = _kitchen(client, db)
    due = client.get(f"{API}/expected").json()
    assert [(o["supplier_name"], o["status"], len(o["lines"])) for o in due] == [("Green Valley Farms", "ordered", 2)]
    lines = _lines(client)
    body = {"po_id": order.id, "client_key": "door-phone-0001", "lines": [
        {"key": lines["Tomatoes, 25 lb case"]["key"], "received": 3, "status": "partial"},   # one case still coming
        {"key": lines["Romaine, 24 ct case"]["key"], "received": 1},                          # one case short, for good
    ]}

    first = client.post(f"{API}/receipts", json=body)
    again = client.post(f"{API}/receipts", json=body)

    assert first.status_code == 201 and first.json()["id"] == again.json()["id"]
    assert (first.json()["order_status"], first.json()["owed"], first.json()["still_coming"]) == ("partial", 1, 1)
    db.expire_all()
    # Three cases of tomatoes and one of romaine, in stock units, once.
    assert (tomatoes.quantity_on_hand, romaine.quantity_on_hand) == (20 + 75, 10 + 24)
    assert db.query(InventoryAdjustment).filter(InventoryAdjustment.source_key.like("receipt:%")).count() == 2
    assert ledger.out_of_balance(db, rid) == []
    # The short line became a message to the rep, drafted and not sent, and a notice.
    claim = db.query(OutreachDraft).filter_by(kind="short_delivery").one()
    assert claim.id == first.json()["claim_draft_id"] and claim.status == "draft" and claim.sent_at is None
    assert "Romaine, 24 ct case: ordered 2 case, 1 usable (short)" in claim.body and "Tomatoes" not in claim.body
    assert [n.kind for n in db.query(Notification).filter_by(restaurant_id=rid)] == ["delivery_short"]

    # The last case of tomatoes arrives: only that line is still open, and the order closes.
    remaining = _lines(client)
    assert list(remaining) == ["Tomatoes, 25 lb case"] and remaining["Tomatoes, 25 lb case"]["expected"] == 1
    done = client.post(f"{API}/receipts", json={"po_id": order.id, "client_key": "door-phone-0002",
                                               "lines": [{"key": remaining["Tomatoes, 25 lb case"]["key"], "received": 1}]}).json()
    db.expire_all()
    assert done["order_status"] == "received" and tomatoes.quantity_on_hand == 120
    assert client.get(f"{API}/expected").json() == []
    assert client.post(f"{API}/receipts", json={**body, "client_key": "door-phone-0003"}).status_code == 409


def test_the_invoice_for_a_delivery_already_checked_in_books_nothing_more(client, db):
    rid, order, tomatoes, romaine, farm = _kitchen(client, db)
    lines = _lines(client)
    client.post(f"{API}/receipts", json={"po_id": order.id, "client_key": "door-phone-0010", "lines": [
        {"key": lines["Tomatoes, 25 lb case"]["key"], "received": 4}, {"key": lines["Romaine, 24 ct case"]["key"], "received": 2}]})
    db.expire_all()
    assert (tomatoes.quantity_on_hand, romaine.quantity_on_hand) == (120, 58)

    saved = client.post("/api/v1/prices/invoices", json={"supplier_name": "Green Valley Farms", "invoice_date": TODAY.isoformat(), "source": "typed", "lines": [
        {"description": "TOMATO ROMA 25#", "qty": 4, "pack_text": "CS", "unit_price": 31.2},
        {"description": "ROMAINE 24CT", "qty": 2, "pack_text": "CS", "unit_price": 28.1}]})
    assert saved.status_code in (200, 201)
    price_stock.book_deliveries(db, rid, TODAY)
    db.commit()
    db.expire_all()

    assert (tomatoes.quantity_on_hand, romaine.quantity_on_hand) == (120, 58)
    assert all(l.invoice_line_id for l in db.get(PurchaseOrder, order.id).lines)


@pytest.mark.parametrize("text, kind, status", [
    ("Confirmed, thanks. See you Thursday.", "confirmed", "confirmed"),
    ("Got your order, will deliver tomorrow morning.", "confirmed", "confirmed"),
    ("Your order is on the truck and out for delivery.", "shipped", "shipped"),
    ("We're out of stock on romaine, can I substitute green leaf?", "change", "ordered"),
    ("Confirmed, but the tomatoes are backordered until Friday.", "change", "ordered"),
    ("Hi, can you call me about this?", "other", "ordered"),
    ("Thanks, confirmed.\n\nOn Mon, Botanica wrote:\n> Please deliver... substitute nothing", "confirmed", "confirmed"),
])
def test_a_suppliers_reply_updates_the_order_or_is_flagged_for_a_person(client, db, text, kind, status):
    rid, order, *_ = _kitchen(client, db)

    out = client.post(f"{API}/orders/{order.id}/reply", json={"text": text}).json()

    db.expire_all()
    assert (out["kind"], order.status) == (kind, status)
    told = [n.kind for n in db.query(Notification).filter_by(restaurant_id=rid)]
    assert told == (["supplier_reply"] if kind in ("change", "other") else [])
    assert order.note.startswith("Green Valley Farms")


def test_a_reply_to_the_orders_own_address_finds_its_order_only_from_the_suppliers_side(client, db, monkeypatch):
    from app.connectors import inbound
    from app.ordering.models import SupplierCalendar
    from app.restaurant import po_mail

    rid, order, _, _, farm = _kitchen(client, db)
    db.add(SupplierCalendar(restaurant_id=rid, supplier_id=farm.id, order_email="orders@greenvalley.example.com"))
    monkeypatch.setattr("app.config.settings.inbound_domain", "in.fullhouse.example")
    address = po_mail.reply_address(order)
    db.commit()
    assert address == f"po-{order.reply_token}@in.fullhouse.example" and len(order.reply_token) == 24

    # Anyone who was ever sent the order holds its reply address. From an address that isn't the supplier's,
    # a reply changes nothing and is said once.
    for n in range(3):
        stranger = inbound.receive(db, {"from": "Mallory <m@evil.example>", "to": address, "text": f"Shipped! Confirmed! {n}"}, f"mail-x{n}")
    db.commit()
    assert stranger["reply"] == "other" and order.status == "ordered" and order.note is None
    assert [n.kind for n in db.query(Notification).filter_by(restaurant_id=rid)] == ["supplier_reply"]

    result = inbound.receive(db, {"from": "Rep <rep@greenvalley.example.com>", "to": address, "subject": "Re: Purchase order", "text": "Shipped this morning."}, "mail-1")
    db.commit()
    assert (result["reply"], result["order_id"], order.status) == ("shipped", order.id, "shipped")

    # A received order is closed: nothing a reply says touches it, note included.
    order.status, order.note = "received", "Sent by autopilot to orders@greenvalley.example.com."
    db.commit()
    inbound.receive(db, {"from": "rep@greenvalley.example.com", "to": address, "text": "We are out of stock, substituting."}, "mail-3")
    db.commit()
    assert order.status == "received" and order.note.startswith("Sent by autopilot")
    assert inbound.receive(db, {"from": "x@example.com", "to": "po-000000000000000000000000@in.fullhouse.example", "text": "Confirmed"}, "mail-2")["reason"] == "unknown order address"


def test_a_restaurant_whose_own_address_begins_po_still_gets_its_mail(client, db, monkeypatch):
    from app.connectors import inbound

    rid, *_ = _kitchen(client, db)
    monkeypatch.setattr("app.config.settings.inbound_domain", "in.fullhouse.example")
    connection = inbound.connection_for(db, rid, create=True)
    connection.settings = {**connection.settings, "slug": "po-boy-shack-a1b2c3", "allowed_senders": ["@sysco.example.com"]}
    db.commit()

    result = inbound.receive(db, {"from": "ar@sysco.example.com", "to": "po-boy-shack-a1b2c3@in.fullhouse.example", "subject": "Invoice", "attachments": []}, "mail-9")

    assert result == {"restaurant_id": rid, "documents": 0}  # reached the restaurant's inbox, not the reply branch


def test_a_late_order_is_said_once_a_day_with_a_chase_message_ready(client, db):
    rid, order, *_ = _kitchen(client, db)
    order.expected_on = TODAY - timedelta(days=2)
    db.commit()

    late = engine.late(db, rid, TODAY)
    engine.late(db, rid, TODAY)
    db.commit()

    assert [(o["days_late"], o["late"]) for o in late] == [(2, True)]
    assert "hasn't arrived" in client.get(f"{API}/expected").json()[0]["chase"]["body"]
    assert [n.title for n in db.query(Notification).filter_by(restaurant_id=rid)] == ["Green Valley Farms order is 2 days late"]


def test_an_order_of_one_item_is_checked_in_in_its_own_unit(client, db):
    rid, _, tomatoes, _, farm = _kitchen(client, db)
    single = orders.place(db, tomatoes, 50, supplier=farm, today=TODAY)
    db.commit()

    due = next(o for o in client.get(f"{API}/expected").json() if o["id"] == single.id)
    assert [(l["key"], l["expected"], l["unit"]) for l in due["lines"]] == [("order", 50, "lb")]
    out = client.post(f"{API}/receipts", json={"po_id": single.id, "client_key": "door-phone-0020", "lines": [{"key": "order", "received": 40}]}).json()

    db.expire_all()
    assert (out["order_status"], out["owed"], tomatoes.quantity_on_hand, single.received_qty) == ("received", 1, 60, 40)


def test_receiving_is_behind_the_flag_and_another_restaurants_orders_read_as_missing(client, db):
    rid, order, tomatoes, *_ = _kitchen(client, db)
    lines = _lines(client)

    other, b = _second_restaurant(db)
    assert b.get(f"{API}/expected").status_code == 404  # the flag is off there
    flags.set_flag(db, other.id, flags.V3_RECEIVING, True)
    db.commit()
    assert b.get(f"{API}/expected").json() == [] and b.get(f"{API}/receipts").json() == []
    body = {"po_id": order.id, "client_key": "not-my-order-01", "lines": [{"key": lines["Tomatoes, 25 lb case"]["key"], "received": 4}]}
    assert b.post(f"{API}/receipts", json=body).status_code == 404
    assert b.post(f"{API}/orders/{order.id}/reply", json={"text": "Confirmed"}).status_code == 404
    db.expire_all()
    assert tomatoes.quantity_on_hand == 20 and order.status == "ordered"


# --- from the correctness review ----------------------------------------------------------------------


def test_a_part_received_order_finished_on_the_inventory_page_books_only_what_was_left(client, db):
    rid, order, tomatoes, romaine, farm = _kitchen(client, db)
    single = orders.place(db, tomatoes, 50, supplier=farm, today=TODAY)
    db.commit()
    lines = _lines(client)
    client.post(f"{API}/receipts", json={"po_id": order.id, "client_key": "door-phone-0030", "lines": [
        {"key": lines["Tomatoes, 25 lb case"]["key"], "received": 1, "status": "partial"}, {"key": lines["Romaine, 24 ct case"]["key"], "received": 2}]})
    client.post(f"{API}/receipts", json={"po_id": single.id, "client_key": "door-phone-0031", "lines": [{"key": "order", "received": 20, "status": "partial"}]})
    db.expire_all()
    assert (tomatoes.quantity_on_hand, single.status, order.status) == (20 + 25 + 20, "partial", "partial")

    # A supplier saying the rest has shipped does not turn "part received" back into "on its way".
    assert client.post(f"{API}/orders/{order.id}/reply", json={"text": "The rest shipped today."}).json()["status"] == "partial"
    # The old Receive button on the Inventory page finishes both: only the remainder is booked.
    assert client.post(f"/api/v1/restaurant/purchase-orders/{single.id}/receive", json={}).status_code == 200
    orders.receive(db, db.get(PurchaseOrder, order.id), received_by="owner@example.com")  # the order page's receive, for an order with lines
    db.commit()
    db.expire_all()
    assert tomatoes.quantity_on_hand == 20 + 100 + 50
    assert (single.status, single.received_qty, order.status) == ("received", 50, "received")


def test_a_reply_that_says_it_cannot_confirm_is_not_a_confirmation_and_a_missing_line_does_not_swallow_its_redelivery(client, db):
    rid, order, tomatoes, romaine, farm = _kitchen(client, db)
    assert engine.classify("We cannot confirm this order for Thursday.") == "change"
    assert engine.classify("Unable to deliver before Friday.") == "change"
    assert engine.classify("Confirmed for Thursday.") == "confirmed"

    lines = _lines(client)
    client.post(f"{API}/receipts", json={"po_id": order.id, "client_key": "door-phone-0040", "lines": [
        {"key": lines["Tomatoes, 25 lb case"]["key"], "received": 4}, {"key": lines["Romaine, 24 ct case"]["key"], "received": 0}]})
    db.expire_all()
    assert romaine.quantity_on_hand == 10
    items = {i.name: i for i in db.query(PriceItem).filter(PriceItem.restaurant_id == rid)}
    # Nothing of the romaine arrived, so nothing checked in can stand for an invoice of it.
    assert engine.received_line_without_invoice(db, rid, items["Romaine, 24 ct case"].id, romaine.id, farm.id, TODAY) is None
    assert engine.received_line_without_invoice(db, rid, items["Tomatoes, 25 lb case"].id, tomatoes.id, farm.id, TODAY) is not None
