"""Checking deliveries in, and reading suppliers' replies to orders."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.platform import flags
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/receiving", tags=["receiving"])


def _scope(db: Session, user: User) -> str:
    """The signed-in user's restaurant, with this feature switched on for it."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.enabled(db, restaurant_id, flags.V3_RECEIVING):
        raise HTTPException(404, "Receiving isn't switched on for this restaurant (Profile, Features).")
    return restaurant_id


from datetime import datetime  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from app.receiving import engine  # noqa: E402
from app.receiving.models import Receipt, ReceiptLine  # noqa: E402
from app.restaurant import inventory  # noqa: E402
from app.restaurant.models import PurchaseOrder  # noqa: E402


class ReceiptLineIn(BaseModel):
    #: The order line's id, or "order" for an order of one item.
    key: str
    received: float = Field(ge=0, le=1_000_000)
    status: str | None = None
    note: str | None = Field(default=None, max_length=200)


class ReceiptIn(BaseModel):
    po_id: str
    client_key: str = Field(min_length=8, max_length=64)
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=255)
    photo_document_id: str | None = None


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


def _order(db: Session, restaurant_id: str, order_id: str) -> PurchaseOrder:
    order = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.id == order_id).first()
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


@router.get("/expected")
def read_expected(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """What is due: every placed order not fully in, with the lines still to check in."""
    return engine.expected(db, _scope(db, user))


@router.post("/receipts", status_code=201)
def check_in(body: ReceiptIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Check a delivery in. Staff do this at the door; the same client_key posts once, however often it is sent."""
    restaurant_id = _scope(db, user)
    order = _order(db, restaurant_id, body.po_id)
    inventory.lock_items(db, [i for i in [order.inventory_item_id, *[l.inventory_item_id for l in order.lines]] if i])
    db.refresh(order)
    try:
        receipt, summary = engine.post_receipt(db, order, [l.model_dump() for l in body.lines], client_key=body.client_key,
                                               received_by=user.email, note=body.note, photo_document_id=body.photo_document_id)
    except engine.ReceiptError as err:
        db.rollback()
        raise HTTPException(409, str(err)) from err
    if flags.enabled(db, restaurant_id, flags.V3_STOCK):
        from app.stock import engine as stock

        stock.rollup(db, restaurant_id, days=3)
    db.commit()
    return {"id": receipt.id, "po_id": order.id, **summary}


@router.get("/receipts")
def recent_receipts(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """The last twenty deliveries checked in, with what each line was."""
    restaurant_id = _scope(db, user)
    out = []
    for receipt in db.query(Receipt).filter(Receipt.restaurant_id == restaurant_id).order_by(Receipt.received_at.desc()).limit(20):
        lines = db.query(ReceiptLine).filter(ReceiptLine.restaurant_id == restaurant_id, ReceiptLine.receipt_id == receipt.id).all()
        out.append({"id": receipt.id, "po_id": receipt.po_id, "supplier_name": receipt.supplier_name or "A supplier", "received_at": receipt.received_at,
                    "received_by": receipt.received_by, "note": receipt.note, "has_photo": bool(receipt.photo_document_id),
                    "lines": [{"description": l.description, "expected": l.expected_qty, "received": l.received_qty, "unit": l.unit, "status": l.status,
                               "note": l.note} for l in lines]})
    return out


@router.post("/orders/{order_id}/reply")
def supplier_replied(order_id: str, body: ReplyIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Paste what the supplier said about an order: a confirmation or a ship notice updates it; anything else is flagged."""
    order = _order(db, _scope(db, user), order_id)
    kind = engine.handle_reply(db, order, body.text, user.email)
    db.commit()
    return {"kind": kind, "status": order.status, "note": order.note, "at": datetime.now().isoformat()}
