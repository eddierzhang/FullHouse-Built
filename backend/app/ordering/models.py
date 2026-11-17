"""Ordering on a schedule: each supplier's calendar, the owner's autopilot limits, every decision made under them, and the outbox."""

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



#: What became of a draft the policy looked at.
PASSED, HELD, SENT, CANCELLED, FAILED = "passed", "held", "sent", "cancelled", "failed"


class SupplierCalendar(Base):
    """When a supplier takes orders and delivers, and what the smallest order is."""

    __tablename__ = "supplier_calendars"
    __table_args__ = (UniqueConstraint("restaurant_id", "supplier_id", name="uq_supplier_calendars_supplier"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_supplier_calendars_restaurant"), nullable=False)
    supplier_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Weekdays orders go in, Monday = 0.
    order_days: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: The supplier's cutoff on an order day, "HH:MM" on the restaurant's clock.
    cutoff_time: Mapped[str] = mapped_column(String(5), default="14:00", nullable=False)
    delivery_days: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_dollars: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum_cases: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: How they take orders: email | portal | phone. Only email can go out by itself.
    channel: Mapped[str] = mapped_column(String(10), default="email", nullable=False)
    order_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class AutopilotPolicy(Base):
    """The owner's limits for one supplier. Off unless an owner switched it on."""

    __tablename__ = "autopilot_policies"
    __table_args__ = (UniqueConstraint("restaurant_id", "supplier_id", name="uq_autopilot_policies_supplier"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_autopilot_policies_restaurant"), nullable=False)
    supplier_id: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    per_order_cap: Mapped[float] = mapped_column(Float, default=300.0, nullable=False)
    weekly_cap: Mapped[float] = mapped_column(Float, default=900.0, nullable=False)
    #: A line whose price rose more than this since the last invoice holds the order.
    max_price_rise_pct: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    #: A line more than this many times its eight-week median quantity holds the order.
    max_qty_multiple: Mapped[float] = mapped_column(Float, default=1.5, nullable=False)
    #: Times an item must have been bought from this supplier in eight weeks.
    min_history: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    cancel_window_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    #: Sends that failed or were cancelled in a row; three switch autopilot off.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paused_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class AutopilotSettings(Base):
    """One row per restaurant: the pause-all switch."""

    __tablename__ = "autopilot_settings"
    __table_args__ = (UniqueConstraint("restaurant_id", name="uq_autopilot_settings_restaurant"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_autopilot_settings_restaurant"), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paused_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AutopilotDecision(Base):
    """What the policy made of one drafted order: every rule, whether it passed, and what happened next."""

    __tablename__ = "autopilot_decisions"
    __table_args__ = (UniqueConstraint("restaurant_id", "po_id", name="uq_autopilot_decisions_order"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_autopilot_decisions_restaurant"), nullable=False)
    po_id: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    #: [{"rule": str, "ok": bool, "detail": str}], every rule, in order.
    rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    #: When the cancel window closes and the order goes out.
    send_after: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(254), nullable=True)


class Outbox(Base):
    """A message to send, written in the same transaction as the change that caused it, delivered by a job.
    A crash between the two can delay a message; it cannot lose or double one."""

    __tablename__ = "outbox"
    __table_args__ = (UniqueConstraint("restaurant_id", "dedupe_key", name="uq_outbox_dedupe"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_outbox_restaurant"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # po_email|po_cancel|notice
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)  # pending|sent|failed|cancelled
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
