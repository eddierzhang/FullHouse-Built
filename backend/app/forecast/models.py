"""Item-level demand forecasts, how they did against actuals, and the prep sheets made from them."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "pos_item_id", "date", name="uq_forecasts_restaurant_item_date"),
        Index("ix_forecasts_restaurant_date", "restaurant_id", "date"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_forecasts_restaurant"), nullable=False)
    pos_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    pos_item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    qty_pred: Mapped[float] = mapped_column(Float, nullable=False)
    qty_lo: Mapped[float] = mapped_column(Float, nullable=False)
    qty_hi: Mapped[float] = mapped_column(Float, nullable=False)
    model: Mapped[str] = mapped_column(String(40), default="weekday-weighted-v1", nullable=False)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


class ForecastError(Base):
    __tablename__ = "forecast_errors"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "pos_item_id", "date", name="uq_forecast_errors_restaurant_item_date"),
        Index("ix_forecast_errors_restaurant_date", "restaurant_id", "date"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_forecast_errors_restaurant"), nullable=False)
    pos_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    qty_pred: Mapped[float] = mapped_column(Float, nullable=False)
    qty_actual: Mapped[float] = mapped_column(Float, nullable=False)
    abs_pct_err: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class PrepSheet(Base):
    __tablename__ = "prep_sheets"
    __table_args__ = (Index("ix_prep_sheets_restaurant_date", "restaurant_id", "date"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_prep_sheets_restaurant"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    #: [{"item_id", "item_name", "qty", "unit", "from": [{"recipe", "plates"}]}]
    lines: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="proposed", nullable=False)  # proposed|confirmed|printed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
