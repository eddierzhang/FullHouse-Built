"""Where the invoices meet the stock.

A price item (what the invoices call it) is counted as an inventory item
(what the kitchen calls it), matched by name the first time it is seen and
changeable by the owner on the item's page. From then on:

- A line on a confirmed invoice, once matched to an item, is booked into
  stock as a delivery: once, in the agent's name, with the invoice in the
  note. Only invoices from the last BOOK_DAYS count, so a back-fill of old
  invoices doesn't inflate the stock.
- An item at or below its reorder point, with a price on a recent invoice,
  gets a purchase order proposed under Recommendations: from the cheapest
  supplier of the last COMPARE_DAYS (the current one for beer, wine and
  liquor, which can't be moved), in whole packs, with the cost it would
  come to. One per item; an Assistant Manager's own proposal for the same
  item stands and is not doubled. A delivery that lifts the stock back
  above the point withdraws the proposal.

Nothing here orders anything: a proposal waits for an owner or admin, and
approval books the goods as received, as it always has.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.agents import crud as agent_crud
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentAction, AgentDefinition, AgentRun
from app.audit.context import AGENT, Actor, actor_context
from app.prices import engine, schemas
from app.prices.models import Invoice, InvoiceLine, PriceItem
from app.restaurant import orders
from app.restaurant import schemas as restaurant_schemas
from app.restaurant.inventory import items_in, lock_items, plural, stock_change
from app.restaurant.models import InventoryItem, PurchaseOrder, Supplier

logger = logging.getLogger(__name__)

#: Invoices older than this when they arrive are history, not deliveries.
BOOK_DAYS = 7
#: Marks a proposal as this module's, so it can be updated or withdrawn; the agent's own are left alone.
SOURCE = "prices"
REORDER_TYPES = ("inventory_reorder", "purchase_order")

#: Units that all mean "one of them": a 24 ct case of romaine is 24 heads.
COUNT_UNITS = frozenset({"ct", "each", "ea", "head", "bunch", "loaf", "bottle", "can", "jar", "piece", "pc", "tray", "pk"})
#: Grams and millilitres per unit, so a case priced in gal can be counted in L.
MASS = {"g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592, "lbs": 453.592}
VOLUME = {"ml": 1.0, "l": 1000.0, "fl oz": 29.5735, "qt": 946.353, "gal": 3785.41}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def name_key(name: str) -> frozenset[str]:
    """The words that name the thing, singular, without the pack: "Tomatoes, 25 lb case" -> {tomato}."""
    return frozenset(_stem(w) for w in engine.words(name.split(",")[0]))


def match_inventory(item: PriceItem, candidates: list[InventoryItem]) -> InventoryItem | None:
    """The one inventory item the price item's name points at; None when there isn't exactly one."""
    key = name_key(item.name)
    if not key:
        return None
    hits = []
    for candidate in candidates:
        other = name_key(candidate.name)
        if other and (key <= other or other <= key):
            hits.append((0 if key == other else 1, candidate))
    if not hits:
        return None
    hits.sort(key=lambda h: (h[0], h[1].name))
    best = [c for rank, c in hits if rank == hits[0][0]]
    return best[0] if len(best) == 1 else None


def units_factor(item: PriceItem, stock: InventoryItem) -> float | None:
    """How many of the stock item's unit one of the price item's unit is; None when they don't convert."""
    a, b = item.unit.strip().lower(), stock.unit.strip().lower()
    if a == b or (a in COUNT_UNITS and b in COUNT_UNITS):
        return 1.0
    for table in (MASS, VOLUME):
        if a in table and b in table:
            return table[a] / table[b]
    return None


def link_items(db: Session, restaurant_id: str | None) -> int:
    """Count every price item with no link as the inventory item its name matches, if one does."""
    stock = items_in(db, restaurant_id)
    if not stock:
        return 0
    linked = 0
    for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.inventory_item_id.is_(None),
                                           PriceItem.inventory_link == "auto"):
        match = match_inventory(item, stock)
        if match is not None:
            item.inventory_item_id = match.id
            linked += 1
    if linked:
        db.flush()
    return linked


def set_link(db: Session, item: PriceItem, inventory_item_id: str | None) -> None:
    """The owner's word on what an item is counted as (None: not counted). Kept over any name match."""
    item.inventory_item_id = inventory_item_id
    item.inventory_link = "owner"
    db.flush()


# --- deliveries ----------------------------------------------------------------------------


def book_deliveries(db: Session, restaurant_id: str | None, today: date) -> list[InvoiceLine]:
    """Book what recent, confirmed, matched invoice lines delivered into stock. Each line once."""
    since = today - timedelta(days=BOOK_DAYS)
    rows = (
        db.query(InvoiceLine, Invoice)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .filter(InvoiceLine.restaurant_id == restaurant_id, Invoice.status == engine.COMPLETE,
                Invoice.invoice_date >= since, Invoice.invoice_date <= today,
                InvoiceLine.item_id.isnot(None), InvoiceLine.pack_size.isnot(None), InvoiceLine.stocked_at.is_(None))
        .order_by(Invoice.invoice_date, InvoiceLine.line_no)
        .all()
    )
    if not rows:
        return []
    suppliers = {s.id: s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    now = datetime.now(timezone.utc)
    booked: list[InvoiceLine] = []
    with actor_context(Actor(type=AGENT, id=AGENT_KEY, restaurant_id=restaurant_id)):
        for line, invoice in rows:
            item = db.get(PriceItem, line.item_id)
            stock = db.get(InventoryItem, item.inventory_item_id) if item and item.inventory_item_id else None
            if stock is None or stock.restaurant_id != restaurant_id:
                continue
            factor = units_factor(item, stock)
            if factor is None:
                continue  # counted in a unit the invoice can't be read in; said on the item's page
            packs = line.delivered_qty if line.delivered_qty is not None else line.qty
            units = round(packs * line.pack_size * factor, 6)
            if units <= 0:
                line.stocked_at = now
                continue
            supplier_name = suppliers.get(line.supplier_id, "Supplier")
            note = f"{supplier_name} invoice of {engine.short_date(invoice.invoice_date)}: {line.description}"
            # An order placed for this item: the invoice is its delivery, and receives it.
            order = orders.open_for(db, restaurant_id, stock.id, line.supplier_id)
            if order is not None:
                orders.receive(db, order, quantity=units, note=note, received_by=AGENT_KEY, invoice_line_id=line.id)
                line.stocked_at = now
                booked.append(line)
                continue
            # Received by hand already, and this is the invoice for it: counted once.
            earlier = orders.received_without_invoice(db, restaurant_id, stock.id, line.supplier_id, today)
            if earlier is not None:
                earlier.invoice_line_id = line.id
                line.stocked_at = now
                continue
            lock_items(db, [stock.id])
            db.refresh(stock)
            after = round(stock.quantity_on_hand + units, 6)
            if after > restaurant_schemas.MAX_QUANTITY:
                logger.warning("not booking %s %s of %s: over the stock limit", units, stock.unit, stock.name)
                line.stocked_at = now
                continue
            stock.quantity_on_hand = after
            with stock_change("delivery", note[:255]):
                db.flush()
            line.stocked_at = now
            booked.append(line)
    db.flush()
    return booked


# --- reorders ------------------------------------------------------------------------------


@dataclass
class Choice:
    supplier_id: str
    per_unit: float
    day: date
    suppliers: int


def cheapest(facts: engine.ItemFacts, today: date) -> Choice | None:
    """The supplier to order from: the lowest latest price of the last COMPARE_DAYS."""
    since = today - timedelta(days=engine.COMPARE_DAYS)
    recent = [p for p in facts.priced if p.day > since] or list(facts.priced)
    if not recent:
        return None
    latest_by_supplier: dict[str, engine.Priced] = {}
    for p in sorted(recent, key=lambda p: (p.day, p.line.line_no)):
        latest_by_supplier[p.supplier_id] = p
    item = facts.item
    if item.category in schemas.SINGLE_SOURCE and facts.latest:
        latest_by_supplier = {facts.latest.supplier_id: latest_by_supplier.get(facts.latest.supplier_id, facts.latest)}
    elif item.moved_to_supplier_id in latest_by_supplier:
        # The owner said they are moving it: order from there.
        latest_by_supplier = {item.moved_to_supplier_id: latest_by_supplier[item.moved_to_supplier_id]}
    best = min(latest_by_supplier.values(), key=lambda p: (p.per_unit, -p.day.toordinal()))
    return Choice(best.supplier_id, best.per_unit, best.day, len({p.supplier_id for p in recent}))


def _usd(value: float) -> str:
    return engine.money(value)


@dataclass
class Proposal:
    stock: InventoryItem
    item: PriceItem
    choice: Choice
    packs: int
    quantity: float
    cost: float
    payload: dict = field(default_factory=dict)


def _proposal(stock: InventoryItem, item: PriceItem, facts: engine.ItemFacts, suppliers: dict[str, Supplier],
              today: date) -> Proposal | None:
    factor = units_factor(item, stock)
    choice = cheapest(facts, today) if factor is not None else None
    if choice is None:
        return None
    units_per_pack = item.pack_size * factor
    if units_per_pack <= 0:
        return None
    need = stock.reorder_qty if stock.reorder_qty > 0 else max(stock.reorder_threshold * 2 - stock.quantity_on_hand, units_per_pack)
    packs = max(1, math.ceil(need / units_per_pack - 1e-9))
    quantity = round(packs * units_per_pack, 6)
    per_pack = engine.per_pack(item, choice.per_unit)
    cost = round(packs * per_pack, 2)
    supplier = suppliers.get(choice.supplier_id)
    supplier_name = supplier.name if supplier else "Supplier"
    among = (f"the lowest of {choice.suppliers} suppliers in the last {engine.COMPARE_DAYS // 7} weeks" if choice.suppliers > 1
             else f"the only supplier on the invoices in the last {engine.COMPARE_DAYS // 7} weeks")
    if item.category in schemas.SINGLE_SOURCE:
        among = "its supplier"
    elif item.moved_to_supplier_id == choice.supplier_id and choice.suppliers > 1:
        among = "where you said you are moving it"
    note = (
        f"{stock.quantity_on_hand:g} {stock.unit} on hand, reorder point {stock.reorder_threshold:g} {stock.unit}. "
        f"{supplier_name} charged {_usd(per_pack)} a {item.pack_unit} ({engine.unit_words(item.pack_size, item.unit)}) "
        f"on {engine.short_date(choice.day)}, {among}. {packs} {plural(item.pack_unit, packs)} comes to about {_usd(cost)}."
    )
    payload = {
        "supplier_id": choice.supplier_id, "supplier_name": supplier_name,
        "inventory_item_id": stock.id, "item_name": stock.name, "quantity": quantity, "note": note,
        "source": SOURCE, "price_item_id": item.id, "packs": packs, "pack_unit": item.pack_unit,
        "price_per_pack": per_pack, "estimated_cost": cost, "priced_on": choice.day.isoformat(),
    }
    return Proposal(stock, item, choice, packs, quantity, cost, payload)


def _pending_reorders(db: Session, restaurant_id: str | None) -> dict[str, list[AgentAction]]:
    out: dict[str, list[AgentAction]] = {}
    for action in agent_crud.list_actions(db, restaurant_id, "pending"):
        if action.action_type in REORDER_TYPES:
            stock_id = (action.payload or {}).get("inventory_item_id")
            if stock_id:
                out.setdefault(str(stock_id), []).append(action)
    return out


def _run_for(db: Session, restaurant_id: str | None, trigger: str, summary: str) -> AgentRun | None:
    """The run the proposals hang off: finished, in the Assistant Manager's name, saying what it did."""
    definition = db.query(AgentDefinition).filter(AgentDefinition.key == AGENT_KEY).first()
    if definition is None:
        return None
    now = datetime.now(timezone.utc)
    run = AgentRun(
        restaurant_id=restaurant_id, agent_definition_id=definition.id, status="succeeded", trigger_type="scheduled",
        input=f"Reorder check: {trigger}" if trigger else "Reorder check", output_summary=summary,
        started_at=now, finished_at=now,
    )
    db.add(run)
    db.flush()
    for kind, payload in (("status_change", {"status": "running"}), ("log", {"text": run.input}),
                          ("log", {"text": summary}), ("status_change", {"status": "succeeded"})):
        agent_crud.add_event(db, run.id, kind, payload)
    return run


def propose_reorders(db: Session, restaurant_id: str | None, today: date, trigger: str = "") -> tuple[list[AgentAction], list[AgentAction]]:
    """Propose a purchase order for each low item the invoices can price; withdraw the ones no longer needed."""
    stock_items = items_in(db, restaurant_id)
    if not stock_items:
        return [], []
    linked = {
        item.inventory_item_id: item
        for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.inventory_item_id.isnot(None))
    }
    pending = _pending_reorders(db, restaurant_id)
    on_order = {o.inventory_item_id: o for o in orders.open_orders(db, restaurant_id)}
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    facts_all = engine.gather(db, restaurant_id, today)
    created: list[AgentAction] = []
    withdrawn: list[AgentAction] = []
    proposals: list[Proposal] = []
    for stock in stock_items:
        waiting = pending.get(stock.id, [])
        ours = [a for a in waiting if (a.payload or {}).get("source") == SOURCE]
        theirs = [a for a in waiting if a not in ours]
        if stock.quantity_on_hand > stock.reorder_threshold:
            for action in ours:
                agent_crud.mark_action_decided(
                    db, action, "rejected",
                    f"Withdrawn: {stock.name} is at {stock.quantity_on_hand:g} {stock.unit}, above its reorder point of "
                    f"{stock.reorder_threshold:g} {stock.unit}.",
                )
                withdrawn.append(action)
            continue
        if stock.id in on_order:
            placed = on_order[stock.id]
            for action in ours:
                agent_crud.mark_action_decided(
                    db, action, "rejected",
                    f"Withdrawn: {orders.describe(placed)} is already on order"
                    + (f", expected {engine.short_date(placed.expected_on)}." if placed.expected_on else "."),
                )
                withdrawn.append(action)
            continue
        if theirs:
            continue  # the Assistant Manager already proposed one; one decision is enough
        item = linked.get(stock.id)
        facts = facts_all.get(item.id) if item else None
        if item is None or facts is None or not facts.priced:
            continue
        proposal = _proposal(stock, item, facts, suppliers, today)
        if proposal is None:
            continue
        if ours:
            existing = ours[0]
            if any((existing.payload or {}).get(k) != proposal.payload[k] for k in ("supplier_id", "quantity", "estimated_cost")):
                existing.payload = proposal.payload
                db.add(existing)
            continue
        proposals.append(proposal)
    if proposals:
        names = ", ".join(f"{p.stock.name} from {p.payload['supplier_name']}" for p in proposals)
        summary = (f"{len(proposals)} item{'s' if len(proposals) != 1 else ''} at or below the reorder point, priced from the "
                   f"invoices: {names}. Proposed, not ordered.")
        run = _run_for(db, restaurant_id, trigger, summary)
        if run is not None:
            for proposal in proposals:
                action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id,
                                     action_type="purchase_order", payload=proposal.payload)
                db.add(action)
                created.append(action)
    db.flush()
    return created, withdrawn


# --- all of it -----------------------------------------------------------------------------


@dataclass
class SyncResult:
    booked: list[InvoiceLine]
    proposed: list[AgentAction]
    withdrawn: list[AgentAction]


def sync(db: Session, restaurant_id: str | None, today: date | None = None, trigger: str = "") -> SyncResult:
    """Link, book, propose. Safe to run after anything that changes invoices or stock. Does not commit."""
    today = today or date.today()
    link_items(db, restaurant_id)
    booked = book_deliveries(db, restaurant_id, today)
    proposed, withdrawn = propose_reorders(db, restaurant_id, today, trigger)
    return SyncResult(booked, proposed, withdrawn)


def mark_counted(db: Session, restaurant_id: str | None) -> None:
    """Treat every line so far as already in the stock figures: for seeds and back-fills."""
    now = datetime.now(timezone.utc)
    for line in db.query(InvoiceLine).filter(InvoiceLine.restaurant_id == restaurant_id, InvoiceLine.stocked_at.is_(None)):
        line.stocked_at = now
    db.flush()


# --- what the pages show -------------------------------------------------------------------


def order_brief(order: PurchaseOrder | None, today: date | None = None) -> schemas.OrderBrief | None:
    if order is None:
        return None
    return schemas.OrderBrief(
        id=order.id, quantity=order.quantity, unit=order.unit, supplier_name=order.supplier_name, status=order.status,
        expected_on=order.expected_on, overdue=orders.is_overdue(order, today),
    )


def link_out(db: Session, item: PriceItem) -> schemas.StockLink | None:
    if not item.inventory_item_id:
        return None
    stock = db.get(InventoryItem, item.inventory_item_id)
    if stock is None:
        return None
    pending = _pending_reorders(db, item.restaurant_id).get(stock.id, [])
    placed = next((o for o in orders.open_orders(db, item.restaurant_id) if o.inventory_item_id == stock.id), None)
    return schemas.StockLink(
        inventory_item_id=stock.id, name=stock.name, unit=stock.unit, quantity_on_hand=stock.quantity_on_hand,
        reorder_threshold=stock.reorder_threshold, low_stock=stock.quantity_on_hand <= stock.reorder_threshold,
        converts=units_factor(item, stock) is not None, pending_action_id=pending[0].id if pending else None,
        on_order=order_brief(placed),
    )


def rows(db: Session, restaurant_id: str | None, today: date | None = None) -> list[schemas.StockRow]:
    """One row per stock item the invoices price: the last price in the kitchen's unit, and any reorder waiting."""
    today = today or date.today()
    stock_items = {s.id: s for s in items_in(db, restaurant_id)}
    suppliers = {s.id: s for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)}
    pending = _pending_reorders(db, restaurant_id)
    on_order = {o.inventory_item_id: o for o in orders.open_orders(db, restaurant_id)}
    facts_all = engine.gather(db, restaurant_id, today)
    out = []
    for item in db.query(PriceItem).filter(PriceItem.restaurant_id == restaurant_id, PriceItem.inventory_item_id.isnot(None)):
        stock = stock_items.get(item.inventory_item_id)
        if stock is None:
            continue
        facts = facts_all.get(item.id)
        latest = facts.latest if facts else None
        factor = units_factor(item, stock)
        waiting = pending.get(stock.id, [])
        out.append(schemas.StockRow(
            inventory_item_id=stock.id, item_id=item.id, item_name=item.name, unit=stock.unit, converts=factor is not None,
            per_unit=round(latest.per_unit / factor, 4) if latest and factor else None,
            last_supplier=engine._ref(suppliers.get(latest.supplier_id)) if latest else None,
            last_date=latest.day if latest else None,
            pending_action_id=waiting[0].id if waiting else None,
            on_order=order_brief(on_order.get(stock.id), today),
        ))
    return out
