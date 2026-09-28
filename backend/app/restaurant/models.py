import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.db.types import UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Whose stock this is. Null only for sample data loaded before the
    #: restaurant was set up; setup claims it. See app/restaurant/inventory.py.
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_inventory_items_restaurant_id_restaurants"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity_on_hand: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    reorder_threshold: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    reorder_qty: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=True)
    unit_cost: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)

    supplier: Mapped[Supplier | None] = relationship()


class InventoryAdjustment(Base):
    """One change to an item's stock: how much, why, by whom, and when.

    Written for every change to `quantity_on_hand`, whatever made it -- a
    delivery logged on the Inventory page, an order drawing ingredients
    down, an approved agent reorder, a revert -- by the listener in
    app/restaurant/inventory.py. So the history always adds up to the
    number on hand.
    """

    __tablename__ = "inventory_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_inventory_adjustments_restaurant_id_restaurants"),
        nullable=True,
        index=True,
    )
    inventory_item_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE", name="fk_inventory_adjustments_item_id_inventory_items"),
        nullable=False,
        index=True,
    )
    #: Signed: a delivery is positive, stock used is negative.
    change: Mapped[float] = mapped_column(Float, nullable=False)
    quantity_after: Mapped[float] = mapped_column(Float, nullable=False)
    #: One of app.restaurant.inventory.REASONS.
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Who, as the change history names them: human (by email), agent, or system.
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)

    # Also what makes a flush insert a new item before its first adjustment.
    item: Mapped[InventoryItem] = relationship()


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    cost: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Set while a promotion is running: the price to restore, and when.
    regular_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    promo_ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)  # open|completed|cancelled
    channel: Mapped[str] = mapped_column(String(32), default="dine_in", nullable=False)  # dine_in|takeout|delivery
    table_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(32), ForeignKey("orders.id"), nullable=False)
    menu_item_id: Mapped[str] = mapped_column(String(32), ForeignKey("menu_items.id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)

    order: Mapped[Order] = relationship(back_populates="items")
    menu_item: Mapped[MenuItem] = relationship()


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    staff_id: Mapped[str] = mapped_column(String(32), ForeignKey("staff.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)

    staff: Mapped[Staff] = relationship()


class RecipeItem(Base):
    """What one serving of a menu item consumes.

    The link that was missing: without it a dish's cost is a number
    somebody typed, orders never touch stock, and there is no way to know
    how fast an ingredient is being used up.
    """

    __tablename__ = "recipe_items"
    __table_args__ = (UniqueConstraint("menu_item_id", "inventory_item_id", name="uq_recipe_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    menu_item_id: Mapped[str] = mapped_column(String(32), ForeignKey("menu_items.id"), nullable=False)
    inventory_item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("inventory_items.id"), nullable=False
    )
    #: Consumed per serving, in the inventory item's own unit.
    quantity: Mapped[float] = mapped_column(Float, nullable=False)

    menu_item: Mapped[MenuItem] = relationship()
    inventory_item: Mapped[InventoryItem] = relationship()


class Restaurant(Base):
    """Who the restaurant is and how it runs, from the owner's setup.

    One per deployment. Created by the first setup step anyone saves, so
    every column but the bookkeeping is nullable: a half-finished setup is
    a real, resumable state. `completed_steps` records which sections have
    been saved; `setup_completed_at` is set once the owner finishes.
    """

    __tablename__ = "restaurants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)

    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: IANA name, e.g. "America/New_York". Agent schedules run on this clock.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    #: {"mon": {"closed": false, "open": "11:00", "close": "22:00"}, ...}
    opening_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: {"safety_days": 1, "default_lead_time_days": 2, "daily_stock_check": "08:00" | null}
    inventory_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    staff_roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: {"max_shift_hours": 10, "min_rest_hours": 10, "max_weekly_hours": 40, "min_staff_per_open_day": 2}
    scheduling_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    completed_steps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    setup_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    #: When anything in the profile was last saved, and by whom (a users.id).
    #: No foreign key for `updated_by`: users already points here, and a
    #: cycle between the two tables would complicate every create and drop.
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
