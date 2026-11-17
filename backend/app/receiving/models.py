"""Checking a delivery in: one receipt per delivery, a line per thing that was expected or turned up."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)



#: How a line compared with what was ordered.
LINE_STATUSES = ("ok", "short", "over", "substituted", "damaged", "missing", "extra")


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (UniqueConstraint("restaurant_id", "client_key", name="uq_receipts_client_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_receipts_restaurant"), nullable=False)
    po_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The phone's own key: a receipt synced twice after poor signal posts once.
    client_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The packing slip or invoice photo, kept as a document.
    photo_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="posted", nullable=False)  # draft|posted
    received_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ReceiptLine(Base):
    __tablename__ = "receipt_lines"
    __table_args__ = (Index("ix_receipt_lines_restaurant_receipt", "restaurant_id", "receipt_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_receipt_lines_restaurant"), nullable=False)
    receipt_id: Mapped[str] = mapped_column(ForeignKey("receipts.id", name="fk_receipt_lines_receipt", ondelete="CASCADE"), nullable=False)
    po_line_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inventory_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    #: In the stock item's unit.
    expected_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_qty: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="ok", nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
