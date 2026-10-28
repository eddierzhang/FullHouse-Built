"""Purchase-order drafts from the invoices: what you usually buy from each supplier, as one proposal per supplier.

For each supplier with at least four invoices in the last eight weeks: expected quantity per item =
the median weekly quantity (in packs) over those eight weeks, expected price = the latest confirmed
price per pack. One `purchase_order` proposal (cap 3 a week) with lines and the rationale, citing
the invoice lines the medians came from. On approval the order is drafted; a person sends it.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.agents import caps
from app.agents.crud import add_event, get_definition_by_key
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentAction, AgentRun
from app.prices import engine
from app.prices.models import Invoice, PriceItem
from app.restaurant.models import Supplier

MIN_INVOICES = 4


def _system_run(db: Session, restaurant_id: str, text: str) -> AgentRun:
    from datetime import datetime, timezone

    definition = get_definition_by_key(db, AGENT_KEY)
    now = datetime.now(timezone.utc)
    run = AgentRun(restaurant_id=restaurant_id, agent_definition_id=definition.id, status="succeeded", trigger_type="scheduled",
                   input=text, output_summary=text, started_at=now, finished_at=now, cost_usd=0.0)
    db.add(run)
    db.flush()
    for kind, payload in (("status_change", {"status": "running"}), ("log", {"text": text}), ("status_change", {"status": "succeeded"})):
        add_event(db, run.id, kind, payload)
    return run


def lines_for(db: Session, restaurant_id: str, supplier: Supplier, today: date, forecast_adjust: bool = True) -> list[dict]:
    """The supplier's usual order: median weekly packs per item over eight weeks, at the latest price."""
    facts = engine.gather(db, restaurant_id, today)
    weeks = [today - timedelta(days=7 * (i + 1)) for i in range(engine.WINDOW_WEEKS)]
    out = []
    for item_id, f in facts.items():
        mine = [p for p in f.priced if p.supplier_id == supplier.id]
        if not mine:
            continue
        item: PriceItem = f.item
        per_week = []
        for start in weeks:
            end = start + timedelta(days=7)
            per_week.append(sum(p.line.qty for p in mine if start < p.day <= end))
        median = statistics.median(per_week) if per_week else 0
        if median <= 0:
            continue
        latest = max(mine, key=lambda p: (p.day, p.line.line_no))
        packs = max(1, math.ceil(median - 1e-9))
        stock_id = item.inventory_item_id
        from app.prices import stock as stock_module
        from app.restaurant.models import InventoryItem

        units = None
        if stock_id:
            stock = db.get(InventoryItem, stock_id)
            factor = stock_module.units_factor(item, stock) if stock else None
            units = round(packs * item.pack_size * factor, 4) if factor else None
        out.append({
            "item_id": item.id, "inventory_item_id": stock_id, "description": item.name, "qty": packs, "unit": item.pack_unit,
            "units": units, "unit_price_expected": engine.per_pack(item, latest.per_unit), "median_weekly_packs": median,
            "cited_line_ids": [p.line.id for p in sorted(mine, key=lambda p: p.day)[-8:]],
        })
    return sorted(out, key=lambda l: -(l["qty"] * (l["unit_price_expected"] or 0)))


def propose(db: Session, restaurant_id: str, supplier: Supplier, today: date | None = None, run: AgentRun | None = None,
            min_invoices: int = MIN_INVOICES) -> AgentAction | None:
    """One drafted order for this supplier, as a proposal; None when there is too little history or the cap is reached."""
    today = today or date.today()
    since = today - timedelta(weeks=engine.WINDOW_WEEKS)
    count = db.query(Invoice).filter(Invoice.restaurant_id == restaurant_id, Invoice.supplier_id == supplier.id,
                                     Invoice.invoice_date > since, Invoice.status == engine.COMPLETE).count()
    if count < min_invoices:
        return None
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "purchase_order",
                                               AgentAction.status == "pending"):
        if (action.payload or {}).get("supplier_id") == supplier.id and (action.payload or {}).get("lines"):
            return action
    lines = lines_for(db, restaurant_id, supplier, today)
    if not lines:
        return None
    if not caps.within(db, restaurant_id, "purchase_order", today):
        return None
    total = round(sum(l["qty"] * (l["unit_price_expected"] or 0) for l in lines), 2)
    rationale = (f"Your usual week from {supplier.name}, from {count} invoices over the last {engine.WINDOW_WEEKS} weeks: "
                 + "; ".join(f"{l['description']} {l['qty']:g} {l['unit']} (median {l['median_weekly_packs']:g} a week) at {engine.money(l['unit_price_expected'] or 0)}" for l in lines[:6])
                 + (f"; and {len(lines) - 6} more" if len(lines) > 6 else "") + f". About {engine.money(total)}. Drafted, not sent.")
    run = run or _system_run(db, restaurant_id, f"Order draft for {supplier.name}")
    action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="purchase_order", payload={
        "supplier_id": supplier.id, "supplier_name": supplier.name, "item_name": f"{len(lines)} items", "quantity": sum(l["qty"] for l in lines),
        "lines": lines, "expected_total": total, "note": rationale, "source": "po_draft",
        "cited_line_ids": [i for l in lines for i in l["cited_line_ids"]][:40],
    })
    db.add(action)
    db.flush()
    return action


def run_all(db: Session, restaurant_id: str, today: date | None = None) -> list[AgentAction]:
    """The weekly workflow: a draft per supplier with enough history, biggest first, within the cap."""
    today = today or date.today()
    since = today - timedelta(weeks=engine.WINDOW_WEEKS)
    made = []
    for supplier in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id).order_by(Supplier.name):
        action = propose(db, restaurant_id, supplier, today)
        if action is not None and action.created_at and action.id not in {a.id for a in made}:
            made.append(action)
    return made
