"""Recipes (menu items and sub-recipes), their lines, and the weekly plate cost computed from price points."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


PROPOSED, CONFIRMED = "proposed", "confirmed"


class Recipe(Base):
    __tablename__ = "recipes"
    __table_args__ = (Index("ix_recipes_restaurant_status", "restaurant_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_recipes_restaurant"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The POS item it is sold as (sales_items.pos_item_id), when it is a dish rather than a sub-recipe.
    pos_item_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The app's own menu item, when it is one.
    menu_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    yield_qty: Mapped[float] = mapped_column(Float, default=1, nullable=False)
    yield_unit: Mapped[str] = mapped_column(String(32), default="plate", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default=PROPOSED, nullable=False)
    source: Mapped[str] = mapped_column(String(10), default="agent", nullable=False)  # agent|user|document
    #: The document it was read from, if any.
    document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)

    lines: Mapped[list["RecipeLine"]] = relationship(back_populates="recipe", cascade="all, delete-orphan", order_by="RecipeLine.position",
                                                     foreign_keys="RecipeLine.recipe_id")


class RecipeLine(Base):
    """Exactly one of `item_id` (a price item) or `sub_recipe_id` is set."""

    __tablename__ = "recipe_lines"
    __table_args__ = (Index("ix_recipe_lines_restaurant_recipe", "restaurant_id", "recipe_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_recipe_lines_restaurant"), nullable=False)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id", name="fk_recipe_lines_recipe", ondelete="CASCADE"), nullable=False)
    position: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    item_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("price_items.id"), nullable=True)
    sub_recipe_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("recipes.id"), nullable=True)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The ingredient as the chef wrote it, kept for the page and for re-matching.
    ingredient_text: Mapped[str | None] = mapped_column(String(200), nullable=True)

    recipe: Mapped[Recipe] = relationship(back_populates="lines", foreign_keys=[recipe_id])


class PlateCost(Base):
    __tablename__ = "plate_costs"
    __table_args__ = (
        UniqueConstraint("recipe_id", "week_of", name="uq_plate_costs_recipe_week"),
        Index("ix_plate_costs_restaurant_week", "restaurant_id", "week_of"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", name="fk_plate_costs_restaurant"), nullable=False)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id", name="fk_plate_costs_recipe", ondelete="CASCADE"), nullable=False)
    week_of: Mapped[date] = mapped_column(Date, nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    #: The price points used: [{"item_id", "line_id", "supplier_id", "per_unit", "qty", "unit"}], and sub-recipes.
    basis: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Lines with no price in the window, so the page can say the cost is partial.
    unpriced: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
