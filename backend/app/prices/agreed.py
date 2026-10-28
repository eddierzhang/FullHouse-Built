"""Agreed prices against what was billed.

A line on a confirmed invoice, matched to an item with an agreed price in effect on the invoice
date, billed per unit above the agreed price by more than 1% or two cents, is an overcharge:
an "Owed to you" card with dollar impact = (billed − agreed) × quantity, citing the invoice line
and the price list it came from. This replaces the placeholder credit_owed of the MVP; short
deliveries stay as they were, beside it.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.prices import engine
from app.prices.models import AgreedPrice, PriceItem
from app.restaurant.models import Supplier

TOLERANCE_PCT = 0.01
TOLERANCE_ABS = 0.02


def in_effect(db: Session, restaurant_id: str | None, item_id: str, supplier_id: str, day: date) -> AgreedPrice | None:
    rows = (
        db.query(AgreedPrice).filter(AgreedPrice.restaurant_id == restaurant_id, AgreedPrice.item_id == item_id,
                                     AgreedPrice.supplier_id == supplier_id, AgreedPrice.effective_from <= day)
        .order_by(AgreedPrice.effective_from.desc()).all()
    )
    for row in rows:
        if row.effective_to is None or row.effective_to >= day:
            return row
    return None


def current(db: Session, item: PriceItem, today: date | None = None) -> AgreedPrice | None:
    """The agreed price in effect today for the item, from whichever supplier set it most recently."""
    today = today or date.today()
    rows = (db.query(AgreedPrice).filter(AgreedPrice.restaurant_id == item.restaurant_id, AgreedPrice.item_id == item.id,
                                         AgreedPrice.effective_from <= today).order_by(AgreedPrice.effective_from.desc()).all())
    return next((r for r in rows if r.effective_to is None or r.effective_to >= today), None)


def overcharges(db: Session, facts: engine.ItemFacts, today: date, suppliers: dict[str, Supplier]) -> dict | None:
    """One owed card for an item whose recent lines were billed above the agreed price."""
    item = facts.item
    owed, sources, parts = 0.0, [], []
    for p in sorted(facts.priced, key=lambda p: p.day):
        agreed = in_effect(db, item.restaurant_id, item.id, p.supplier_id, p.day)
        if agreed is None:
            continue
        if p.per_unit <= agreed.unit_price * (1 + TOLERANCE_PCT) and p.per_unit - agreed.unit_price <= TOLERANCE_ABS:
            continue
        over = round((p.per_unit - agreed.unit_price) * p.units - p.line.credited, 2)
        if over <= 0:
            continue
        owed += over
        name = suppliers[p.supplier_id].name if p.supplier_id in suppliers else "the supplier"
        sources.append({"kind": "invoice", "id": p.line.invoice_id, "label": f"{engine.short_date(p.day)}, {name}"})
        if agreed.source_document_id:
            sources.append({"kind": "document", "id": agreed.source_document_id, "label": f"price list from {engine.short_date(agreed.effective_from)}"})
        parts.append(f"{name} billed {engine.money(engine.per_pack(item, p.per_unit))} a {item.pack_unit} on {engine.short_date(p.day)} "
                     f"against the agreed {engine.money(engine.per_pack(item, agreed.unit_price))}, {engine.money(over)} over on {p.units:g} {item.unit}.")
    if owed <= 0:
        return None
    last = max((p for p in facts.priced), key=lambda p: p.day)
    return {
        "section": "owed", "item_id": item.id, "item_name": item.name, "impact": round(owed, 2), "sentence": " ".join(parts[-2:]),
        "action": {"kind": "draft", "label": "Draft message", "draft_kind": "credit_claim", "supplier_id": last.supplier_id},
        "sources": sources[-4:],
    }
