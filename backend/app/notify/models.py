"""What the restaurant was told, and what each restaurant has used of the things that cost money."""

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



class Notification(Base):
    """One thing worth telling someone: low stock, a late order, an order autopilot is about to send."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_restaurant_created", "restaurant_id", "created_at"),
                      UniqueConstraint("restaurant_id", "dedupe_key", name="uq_notifications_dedupe"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_notifications_restaurant"), nullable=False)
    #: Null means everyone who manages the restaurant.
    user_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Where tapping it goes, as a hash route ("#autopilot").
    link: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class UsageMeter(Base):
    """What a restaurant used in a month of something metered: model dollars, voice seconds, messages."""

    __tablename__ = "usage_meters"
    __table_args__ = (UniqueConstraint("restaurant_id", "month", "meter", name="uq_usage_meters_month"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_usage_meters_restaurant"), nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-11"
    meter: Mapped[str] = mapped_column(String(24), nullable=False)  # model_usd|voice_seconds|messages
    amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
