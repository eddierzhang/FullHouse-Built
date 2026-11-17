"""Counting stock: where things are kept, count sessions, and the daily rollup the charts read."""

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



OPEN, POSTED = "open", "posted"


class StorageArea(Base):
    """Where stock is kept (walk-in, dry store, bar), in the order someone walks it."""

    __tablename__ = "storage_areas"
    __table_args__ = (UniqueConstraint("restaurant_id", "name", name="uq_storage_areas_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_storage_areas_restaurant"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class StockCount(Base):
    """One count session: a person walking one area (or everything) and saying what is there."""

    __tablename__ = "stock_counts"
    __table_args__ = (UniqueConstraint("restaurant_id", "client_key", name="uq_stock_counts_client_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_stock_counts_restaurant"), nullable=False)
    storage_area_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=OPEN, nullable=False)
    #: The phone's own key for the session, so a count synced twice after poor signal posts once.
    client_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StockCountLine(Base):
    """One item on a count: what the ledger expected, what was counted, at what cost."""

    __tablename__ = "stock_count_lines"
    __table_args__ = (Index("ix_stock_count_lines_restaurant_count", "restaurant_id", "count_id"),
                      UniqueConstraint("count_id", "inventory_item_id", name="uq_stock_count_lines_item"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_stock_count_lines_restaurant"), nullable=False)
    count_id: Mapped[str] = mapped_column(ForeignKey("stock_counts.id", name="fk_stock_count_lines_count", ondelete="CASCADE"), nullable=False)
    inventory_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_qty: Mapped[float] = mapped_column(Float, nullable=False)
    #: Null until someone counts it; an uncounted line changes nothing when the count is posted.
    counted_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_cost: Mapped[float] = mapped_column(Float, default=0, nullable=False)


class StockDaily(Base):
    """One item, one day: what came in, what sales used, what was wasted, what a count corrected, and the close.
    Written by the rollup job so charts never scan the ledger."""

    __tablename__ = "stock_daily"
    __table_args__ = (UniqueConstraint("restaurant_id", "inventory_item_id", "date", name="uq_stock_daily_item_day"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_stock_daily_restaurant"), nullable=False)
    inventory_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    received: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    used: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    wasted: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    count_change: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    closing: Mapped[float] = mapped_column(Float, default=0, nullable=False)
