"""The stock ledger: every movement of stock is one row of `inventory_adjustments`, and an item's quantity
on hand is the sum of its rows.

The rows are written by the listener in app.restaurant.inventory whenever `quantity_on_hand` changes, so
nothing here inserts an adjustment directly. What this module adds is the rule for automatic movements:
each names the outside fact behind it with a `source_key`, and the same key never moves stock twice. A
replayed webhook, a re-run sync or a count synced twice from a phone therefore changes nothing.

    from app.stock import ledger
    moved = ledger.move(db, item, -2.5, "sale", source_key=f"sale:{day}:{pos_item_id}:{item.id}", note="12 Margherita Pizza")

`move` returns False, and changes nothing, when that key has been seen. Callers commit.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.restaurant.inventory import stock_change
from app.restaurant.models import InventoryAdjustment, InventoryItem


def seen(db: Session, restaurant_id: str | None, source_key: str) -> bool:
    """Whether a movement with this source has already been recorded for the restaurant."""
    return db.query(InventoryAdjustment.id).filter(InventoryAdjustment.restaurant_id == restaurant_id,
                                                   InventoryAdjustment.source_key == source_key).first() is not None


def move(db: Session, item: InventoryItem, change: float, reason: str, *, source_key: str | None = None,
         note: str | None = None, allow_negative: bool = True) -> bool:
    """Change `item`'s stock by `change` (signed, in the item's unit) and record why.

    With a `source_key`, the movement is idempotent: a second call with the same key returns False and
    leaves the stock alone. Stock may go below zero unless `allow_negative` is False, in which case it
    stops at zero: sales really happened, and a negative figure is the honest sign that a delivery or a
    count is missing; a hand entry of waste cannot take away more than is there.
    """
    change = round(float(change), 6)
    if change == 0:
        return False
    if source_key is not None and seen(db, item.restaurant_id, source_key):
        return False
    after = round(item.quantity_on_hand + change, 6)
    if not allow_negative and after < 0:
        after = 0.0
    if after == item.quantity_on_hand:
        return False
    with stock_change(reason, note, source_key=source_key):
        item.quantity_on_hand = after
        db.add(item)
        db.flush()
    return True


def set_to(db: Session, item: InventoryItem, quantity: float, reason: str = "count", *, source_key: str | None = None,
           note: str | None = None) -> float:
    """Set `item`'s stock to `quantity` (a count). Returns the difference recorded, 0 when nothing moved."""
    difference = round(float(quantity) - item.quantity_on_hand, 6)
    return difference if move(db, item, difference, reason, source_key=source_key, note=note) else 0.0


def balance(db: Session, item: InventoryItem) -> float:
    """What the ledger says the item's stock is: the sum of every movement recorded for it."""
    total = db.query(func.coalesce(func.sum(InventoryAdjustment.change), 0.0)).filter(
        InventoryAdjustment.inventory_item_id == item.id).scalar()
    return round(float(total or 0.0), 6)


def out_of_balance(db: Session, restaurant_id: str | None, tolerance: float = 1e-4) -> list[dict]:
    """Items whose quantity on hand is not the sum of their ledger rows. Empty when the ledger is the truth."""
    sums = dict(db.query(InventoryAdjustment.inventory_item_id, func.sum(InventoryAdjustment.change))
                .filter(InventoryAdjustment.restaurant_id == restaurant_id).group_by(InventoryAdjustment.inventory_item_id).all())
    wrong = []
    for item in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id):
        ledger_total = round(float(sums.get(item.id) or 0.0), 6)
        if abs(ledger_total - item.quantity_on_hand) > tolerance:
            wrong.append({"inventory_item_id": item.id, "name": item.name, "on_hand": item.quantity_on_hand, "ledger": ledger_total})
    return wrong
