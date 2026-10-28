"""The compliance and asset calendar: dated things with proof, and the equipment they are about."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


KINDS = ("license", "permit", "inspection", "tax_filing", "warranty", "service")
OPEN, DONE, OVERDUE = "open", "done", "overdue"


class CalendarItem(Base):
    __tablename__ = "calendar_items"
    __table_args__ = (Index("ix_calendar_items_restaurant_due", "restaurant_id", "due_on"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_calendar_items_restaurant"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: "yearly", "monthly", "90 days": how it repeats once done; null for one-offs.
    recurrence: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=OPEN, nullable=False)
    proof_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class Asset(Base):
    """The equipment register: what was bought, when its warranty ends, how often it is serviced."""

    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_restaurant_name", "restaurant_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_assets_restaurant"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purchased_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_service_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class Referral(Base):
    """An owner's referral of another restaurant: the address they gave, and whether it was rewarded."""

    __tablename__ = "referrals"
    __table_args__ = (Index("ix_referrals_restaurant_created", "restaurant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_referrals_restaurant"), nullable=False)
    referred_email: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="invited", nullable=False)  # invited|signed_up|rewarded
    rewarded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
