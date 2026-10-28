"""Where the invoices meet the stock: an item on an invoice is counted as an item in Inventory,
a confirmed invoice books its delivery once, and a low figure gets a reorder proposed at the
price the invoices show. Nothing is ordered."""

from datetime import timedelta

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentAction, AgentDefinition
from app.prices import stock
from app.prices.demo import seed as seed_invoices
from app.prices.models import InvoiceLine, PriceItem
from app.restaurant.models import InventoryAdjustment, InventoryItem
from test_prices import API, TODAY, _lines, _restaurant, _suppliers

STOCK = "/api/v1/restaurant/inventory-items"
ORDERS = "/api/v1/restaurant/purchase-orders"


def _stock(client, name, unit, on_hand, point, reorder_qty=0) -> str:
    made = client.post(STOCK, json={"name": name, "unit": unit, "quantity_on_hand": on_hand,
                                    "reorder_threshold": point, "reorder_qty": reorder_qty})
    assert made.status_code == 201, made.text
    return made.json()["id"]


def _on_hand(client, item_id) -> float:
    return next(i for i in client.get(STOCK).json() if i["id"] == item_id)["quantity_on_hand"]


def _pending(client) -> list[dict]:
    return client.get("/api/v1/agents/actions?status=pending").json()


def _seeded_with_stock(client, db) -> tuple[dict, dict[str, str]]:
    """The demo's eight weeks of invoices, with the kitchen's stock list made first so names match."""
    restaurant = _restaurant(client, db)
    stock_ids = {
        "Tomatoes": _stock(client, "Tomatoes", "lb", 6, 15, 30),
        "Romaine Lettuce": _stock(client, "Romaine Lettuce", "head", 9, 10, 30),
        "Basil": _stock(client, "Basil", "bunch", 3, 8),
        "Olive Oil": _stock(client, "Olive Oil", "L", 8, 4, 12),
        "Flour": _stock(client, "Flour", "lb", 62, 25),
    }
    seed_invoices(db, restaurant.id, _suppliers(db, restaurant), TODAY)
    db.commit()
    return {"id": restaurant.id}, stock_ids


def test_a_new_item_is_counted_as_the_stock_item_its_name_matches_and_the_invoice_books_the_delivery(client, db):
    _restaurant(client, db)
    tomatoes = _stock(client, "Tomatoes", "lb", 6, 15, 30)
    saved = client.post(f"{API}/invoices", json={
        "supplier_name": "Green Valley Farms", "invoice_date": TODAY.isoformat(), "source": "typed",
        "lines": _lines(("TOMATO ROMA 25#", 2, "CS", 27.60)),
    }).json()
    # Nothing is matched yet, so nothing moves: the agent asks first.
    assert _on_hand(client, tomatoes) == 6
    question = saved["first_question"]
    answered = client.post(f"{API}/questions/{question['id']}/answer", json={"answer": "yes"})
    assert answered.status_code == 200, answered.text

    item = db.query(PriceItem).one()
    assert item.inventory_item_id == tomatoes and item.inventory_link == "auto"
    # Two cases of 25 lb arrived: booked once, in the agent's name, with the invoice in the note.
    assert _on_hand(client, tomatoes) == 56
    delivery = db.query(InventoryAdjustment).filter_by(inventory_item_id=tomatoes, reason="delivery").one()
    assert delivery.change == 50 and delivery.actor_type == "agent" and delivery.actor_id == AGENT_KEY
    assert delivery.note.startswith("Green Valley Farms invoice of") and "TOMATO ROMA 25#" in delivery.note
    assert db.query(InvoiceLine).one().stocked_at is not None
    # Running it again changes nothing.
    stock.sync(db, item.restaurant_id, TODAY)
    db.commit()
    assert _on_hand(client, tomatoes) == 56
    assert db.query(InventoryAdjustment).filter_by(inventory_item_id=tomatoes, reason="delivery").count() == 1
    # Stock is well above the reorder point, so nothing is proposed.
    assert _pending(client) == []


def test_old_invoices_are_history_not_deliveries(client, db):
    _restaurant(client, db)
    flour = _stock(client, "Flour", "lb", 62, 25)
    old = (TODAY - timedelta(days=stock.BOOK_DAYS + 1)).isoformat()
    saved = client.post(f"{API}/invoices", json={
        "supplier_name": "Sysco Foods", "invoice_date": old, "source": "typed",
        "lines": _lines(("FLOUR AP 50#", 4, "BG", 18.90)),
    }).json()
    client.post(f"{API}/questions/{saved['first_question']['id']}/answer", json={"answer": "yes"})
    assert _on_hand(client, flour) == 62


def test_a_low_item_gets_a_purchase_order_from_the_cheapest_recent_supplier_in_whole_packs(client, db):
    restaurant, ids = _seeded_with_stock(client, db)
    # The seed is history: the stock figures stand, and the seed itself proposes nothing until something moves.
    assert _on_hand(client, ids["Romaine Lettuce"]) == 9 and _pending(client) == []

    used = client.post(f"{STOCK}/{ids['Basil']}/adjustments", json={"reason": "used", "quantity": 1})
    assert used.status_code == 200, used.text

    pending = _pending(client)
    by_item = {a["payload"]["item_name"]: a for a in pending}
    assert all(a["action_type"] == "purchase_order" and a["payload"]["source"] == "prices" for a in pending)
    # Romaine: 9 heads against a point of 10, reorder quantity 30. Sysco charged $26.00 a case of 24 this week,
    # Green Valley $28.10. Two cases from Sysco cover 30 heads.
    romaine = by_item["Romaine Lettuce"]["payload"]
    assert (romaine["supplier_name"], romaine["packs"], romaine["quantity"], romaine["estimated_cost"]) == ("Sysco Foods", 2, 48, 52.0)
    assert romaine["note"] == (
        "9 head on hand, reorder point 10 head. Sysco Foods charged $26.00 a case (24 ct) on "
        f"{stock.engine.short_date(TODAY - timedelta(days=2))}, the lowest of 2 suppliers in the last 4 weeks. "
        "2 cases comes to about $52.00."
    )
    # Tomatoes: 6 lb against 15, reorder quantity 30 -> two 25 lb cases from whoever charged less per lb last month.
    tomatoes = by_item["Tomatoes"]["payload"]
    assert tomatoes["packs"] == 2 and tomatoes["quantity"] == 50
    assert tomatoes["estimated_cost"] == round(2 * tomatoes["price_per_pack"], 2)
    # Basil is counted in bunches and bought by the pound: nothing is booked or proposed, and the page says so.
    assert "Basil" not in by_item
    basil = next(r for r in client.get(f"{API}/stock").json() if r["inventory_item_id"] == ids["Basil"])
    assert basil["converts"] is False and basil["per_unit"] is None
    # Olive oil is counted in litres and bought by the gallon: that converts.
    oil = next(r for r in client.get(f"{API}/stock").json() if r["inventory_item_id"] == ids["Olive Oil"])
    assert oil["converts"] is True and oil["per_unit"] == round(96.00 / 4 / 3.78541, 4)
    # Each proposal hangs off a finished run that says what it did, in the Assistant Manager's name.
    run = agent_crud.get_run(db, by_item["Romaine Lettuce"]["run_id"])
    assert run.status == "succeeded" and run.input == "Reorder check: Basil used"
    assert "Romaine Lettuce from Sysco Foods" in run.output_summary and "Proposed, not ordered" in run.output_summary
    assert [e.type for e in agent_crud.list_events(db, run.id)] == ["status_change", "log", "log", "status_change"]


def test_a_delivery_that_lifts_the_stock_withdraws_the_proposal(client, db):
    restaurant, ids = _seeded_with_stock(client, db)
    client.post(f"{STOCK}/{ids['Basil']}/adjustments", json={"reason": "used", "quantity": 1})
    romaine = next(a for a in _pending(client) if a["payload"]["item_name"] == "Romaine Lettuce")

    # The order arrives and the invoice comes in: its line is already known, so it books itself.
    saved = client.post(f"{API}/invoices", json={
        "supplier_name": "Sysco Foods", "invoice_date": TODAY.isoformat(), "source": "typed",
        "lines": _lines(("LETT ROMAINE 24CT", 2, "CS", 26.00)),
    })
    assert saved.status_code == 201 and saved.json()["matched"] == 1
    assert _on_hand(client, ids["Romaine Lettuce"]) == 57
    assert "Romaine Lettuce" not in [a["payload"]["item_name"] for a in _pending(client)]
    gone = db.get(AgentAction, romaine["id"])
    assert gone.status == "rejected" and gone.applied_result == (
        "Withdrawn: Romaine Lettuce is at 57 head, above its reorder point of 10 head."
    )


def test_the_assistant_managers_own_proposal_is_not_doubled(client, db):
    restaurant, ids = _seeded_with_stock(client, db)
    definition = db.query(AgentDefinition).filter(AgentDefinition.key == AGENT_KEY).one()
    run = agent_crud.create_run(db, definition.id, restaurant_id=restaurant["id"])
    agent_crud.create_action(db, run.id, definition.id, "inventory_reorder",
                             {"inventory_item_id": ids["Romaine Lettuce"], "item_name": "Romaine Lettuce", "quantity": 30})

    client.post(f"{STOCK}/{ids['Basil']}/adjustments", json={"reason": "used", "quantity": 1})
    romaine = [a for a in _pending(client) if a["payload"]["item_name"] == "Romaine Lettuce"]
    assert [a["action_type"] for a in romaine] == ["inventory_reorder"]


def test_the_owner_decides_what_an_item_is_counted_as(client, db):
    restaurant, ids = _seeded_with_stock(client, db)
    item = db.query(PriceItem).filter_by(name="Romaine, 24 ct case").one()
    detail = client.get(f"{API}/items/{item.id}").json()
    assert detail["stock"]["name"] == "Romaine Lettuce" and detail["stock"]["low_stock"] is True
    assert detail["inventory_link"] == "auto"

    # Not counted: the name match no longer applies, and nothing is booked or proposed for it.
    unlinked = client.patch(f"{API}/items/{item.id}", json={"inventory_item_id": None})
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["stock"] is None and unlinked.json()["inventory_link"] == "owner"
    stock.sync(db, restaurant["id"], TODAY)
    db.commit()
    assert db.get(PriceItem, item.id).inventory_item_id is None

    # Counted as something else: the owner's word, kept over the name.
    relinked = client.patch(f"{API}/items/{item.id}", json={"inventory_item_id": ids["Flour"]})
    assert relinked.json()["stock"]["name"] == "Flour" and relinked.json()["inventory_link"] == "owner"
    assert client.patch(f"{API}/items/{item.id}", json={"inventory_item_id": "nope"}).status_code == 404

    # The pack-size correction still works on its own, and the link stays.
    fixed = client.patch(f"{API}/items/{item.id}", json={"pack_size": 24, "pack_unit": "case"})
    assert fixed.status_code == 200 and fixed.json()["stock"]["name"] == "Flour"


def test_an_ambiguous_name_is_not_guessed(client, db):
    _restaurant(client, db)
    _stock(client, "Tomatoes", "lb", 6, 15)
    _stock(client, "Tomato Sauce", "L", 6, 2)
    saved = client.post(f"{API}/invoices", json={
        "supplier_name": "Green Valley Farms", "invoice_date": TODAY.isoformat(), "source": "typed",
        "lines": _lines(("TOMATO ROMA 25#", 2, "CS", 27.60)),
    }).json()
    client.post(f"{API}/questions/{saved['first_question']['id']}/answer", json={"answer": "yes"})
    item = db.query(PriceItem).one()
    # "Tomato roma" fits "Tomatoes" exactly and "Tomato Sauce" only partly: the exact one wins.
    assert db.get(InventoryItem, item.inventory_item_id).name == "Tomatoes"

    _stock(client, "Cheese", "lb", 1, 2)
    _stock(client, "Cream Cheese", "lb", 1, 2)
    client.post(f"{API}/invoices", json={
        "supplier_name": "Harbor Dairy Co.", "invoice_date": TODAY.isoformat(), "source": "typed",
        "lines": _lines(("CHEESE CREAM 3#", 2, "CS", 12.00)),
    })
    open_question = next(q for q in client.get(f"{API}/questions").json() if q["status"] == "open")
    client.post(f"{API}/questions/{open_question['id']}/answer", json={"answer": "yes"})
    cream = db.query(PriceItem).filter(PriceItem.name.ilike("%cream%")).one()
    assert db.get(InventoryItem, cream.inventory_item_id).name == "Cream Cheese"


def test_approving_places_the_order_and_the_invoice_for_it_receives_it(client, db):
    """The proposals are proposals: approval places an order, and the stock moves when it arrives."""
    restaurant, ids = _seeded_with_stock(client, db)
    client.post(f"{STOCK}/{ids['Basil']}/adjustments", json={"reason": "used", "quantity": 1})
    romaine = next(a for a in _pending(client) if a["payload"]["item_name"] == "Romaine Lettuce")
    approved = client.post(f"/api/v1/agents/actions/{romaine['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["applied_result"].startswith("Drafted an order for 2 cases (48 head) of Romaine Lettuce from Sysco Foods, expected ")
    assert _on_hand(client, ids["Romaine Lettuce"]) == 9

    (order,) = client.get(f"{ORDERS}").json()
    assert (order["item_name"], order["supplier_name"], order["packs"], order["quantity"], order["status"]) == (
        "Romaine Lettuce", "Sysco Foods", 2, 48, "draft")
    assert order["estimated_cost"] == 52.0 and order["action_id"] == romaine["id"]
    # Placed by phone: marked sent. (A draft is not yet an order the supplier knows about.)
    assert client.post(f"{ORDERS}/{order['id']}/mark-sent").json()["status"] == "ordered"
    # On order, so not proposed again while the stock is still low.
    client.post(f"{STOCK}/{ids['Basil']}/adjustments", json={"reason": "used", "quantity": 1})
    assert "Romaine Lettuce" not in [a["payload"]["item_name"] for a in _pending(client)]
    row = next(r for r in client.get(f"{API}/stock").json() if r["inventory_item_id"] == ids["Romaine Lettuce"])
    assert row["on_order"]["quantity"] == 48 and row["on_order"]["status"] == "ordered"

    # The invoice arrives: it is the order's delivery, so it receives the order. Booked once.
    saved = client.post(f"{API}/invoices", json={
        "supplier_name": "Sysco Foods", "invoice_date": TODAY.isoformat(), "source": "typed",
        "lines": _lines(("LETT ROMAINE 24CT", 2, "CS", 26.00)),
    })
    assert saved.status_code == 201 and saved.json()["matched"] == 1
    assert _on_hand(client, ids["Romaine Lettuce"]) == 57
    assert client.get(ORDERS).json() == []
    (done,) = client.get(f"{ORDERS}?status=received").json()
    assert done["received_qty"] == 48 and done["received_by_invoice"] is True and done["received_by"] == AGENT_KEY
    delivery = db.query(InventoryAdjustment).filter_by(inventory_item_id=ids["Romaine Lettuce"]).order_by(InventoryAdjustment.id.desc()).first()
    assert delivery.reason == "reorder" and delivery.change == 48
    assert delivery.note.startswith("Order received: 2 cases (48 head) of Romaine Lettuce from Sysco Foods. Sysco Foods invoice of")


def test_a_hand_receipt_and_then_its_invoice_count_once(client, db):
    restaurant, ids = _seeded_with_stock(client, db)
    placed = client.post(ORDERS, json={"inventory_item_id": ids["Romaine Lettuce"],
                                       "supplier_id": _sysco(db), "quantity": 48, "note": "Phoned in."})
    assert placed.status_code == 201, placed.text
    order = placed.json()
    assert order["status"] == "ordered" and order["expected_on"] == (TODAY + timedelta(days=3)).isoformat()

    # The driver drops it off: received by hand, 46 of 48.
    received = client.post(f"{ORDERS}/{order['id']}/receive", json={"quantity": 46, "note": "Two heads crushed."})
    assert received.status_code == 200, received.text
    assert _on_hand(client, ids["Romaine Lettuce"]) == 55
    # The invoice comes a week later: it is that receipt's invoice, not a second delivery.
    client.post(f"{API}/invoices", json={
        "supplier_name": "Sysco Foods", "invoice_date": TODAY.isoformat(), "source": "typed",
        "lines": _lines(("LETT ROMAINE 24CT", 2, "CS", 26.00)),
    })
    assert _on_hand(client, ids["Romaine Lettuce"]) == 55
    (done,) = client.get(f"{ORDERS}?status=received").json()
    assert done["received_by_invoice"] is True and done["received_qty"] == 46
    assert db.query(InvoiceLine).filter(InvoiceLine.description == "LETT ROMAINE 24CT", InvoiceLine.stocked_at.isnot(None)).count() >= 1


def _sysco(db) -> str:
    from app.restaurant.models import Supplier

    return db.query(Supplier).filter(Supplier.name == "Sysco Foods").one().id
