"""Daily sales and labor as the connectors deliver them, and the weekly P&L computed from them."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SalesDay(Base):
    """One day's totals from one source. A re-sync with the same key overwrites, never doubles."""

    __tablename__ = "sales_days"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "date", "source", name="uq_sales_days_restaurant_date_source"),
        Index("ix_sales_days_restaurant_date", "restaurant_id", "date"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_sales_days_restaurant"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    net_sales: Mapped[float] = mapped_column(Float, nullable=False)
    gross_sales: Mapped[float | None] = mapped_column(Float, nullable=True)
    covers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    tips: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # pos|csv|manual
    connection_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class SalesItem(Base):
    """What sold, by POS item, by day."""

    __tablename__ = "sales_items"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "date", "pos_item_id", "source", name="uq_sales_items_restaurant_date_item_source"),
        Index("ix_sales_items_restaurant_date", "restaurant_id", "date"),
        Index("ix_sales_items_restaurant_item_date", "restaurant_id", "pos_item_id", "date"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_sales_items_restaurant"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    pos_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    pos_item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    net: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class LaborDay(Base):
    """One day's hours and dollars from payroll, a scheduler export, or a CSV. Never produced here."""

    __tablename__ = "labor_days"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "date", "source", name="uq_labor_days_restaurant_date_source"),
        Index("ix_labor_days_restaurant_date", "restaurant_id", "date"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_labor_days_restaurant"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[float] = mapped_column(Float, nullable=False)
    dollars: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # payroll|scheduler|csv|manual
    connection_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class PnlWeek(Base):
    """The week's purchases by category, sales and labor, and the percentages. Nulls where a day is missing."""

    __tablename__ = "pnl_weeks"
    __table_args__ = (UniqueConstraint("restaurant_id", "week_of", name="uq_pnl_weeks_restaurant_week"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_pnl_weeks_restaurant"), nullable=False, index=True)
    week_of: Mapped[date] = mapped_column(Date, nullable=False)
    purchases_food: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    purchases_paper: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    purchases_bev: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    sales_net: Mapped[float | None] = mapped_column(Float, nullable=True)
    labor_dollars: Mapped[float | None] = mapped_column(Float, nullable=True)
    food_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    labor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    prime_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Which days had no sales or no labor, so the digest can say what to connect.
    missing: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class CsvProfile(Base):
    """How a restaurant's CSV columns map to ours, saved after the first upload so the next needs no mapping."""

    __tablename__ = "csv_profiles"
    __table_args__ = (UniqueConstraint("restaurant_id", "capability", name="uq_csv_profiles_restaurant_capability"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_csv_profiles_restaurant"), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(16), nullable=False)  # sales_days|sales_items|labor_days
    #: Our field -> the file's header, plus "format" (square|toast|clover|generic) and "date_format".
    mapping: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
