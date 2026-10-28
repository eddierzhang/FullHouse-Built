import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_suppliers_restaurant_id_restaurants"), nullable=True, index=True
    )
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
    #: Bought in packs: one `pack_unit` (a case, a box) holds `pack_size` of `unit`.
    #: Only for entering deliveries and counts; stock, recipes, reorder points and
    #: cost all stay in `unit`, so nothing downstream has to convert. Both or neither.
    pack_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pack_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)

    supplier: Mapped[Supplier | None] = relationship()


#: A purchase order's life: drafted (approved, not yet sent), placed with the supplier, on its way,
#: in the stock figures, or called off.
DRAFT, ORDERED, SHIPPED, RECEIVED, CANCELLED = "draft", "ordered", "shipped", "received", "cancelled"
OPEN_ORDER_STATUSES = (DRAFT, ORDERED, SHIPPED)


class PurchaseOrder(Base):
    """An order placed with a supplier, followed from approval to receipt.

    Approving a reorder places the order; nothing reaches the stock figures
    until it is received, either by hand on the Inventory page or by the
    invoice for it arriving (app.prices.stock). `quantity` is in the item's
    unit; `packs` and `pack_unit` say how it was ordered when that is known.
    """

    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_purchase_orders_restaurant_id_restaurants"), nullable=True, index=True
    )
    supplier_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("suppliers.id"), nullable=True)
    #: The one stock item, for an order of one thing; an order with lines leaves it empty.
    inventory_item_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("inventory_items.id", name="fk_purchase_orders_inventory_item_id_inventory_items"),
        nullable=True, index=True,
    )
    #: As they read when the order was placed, so a renamed item or supplier doesn't rewrite the record.
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    packs: Mapped[float | None] = mapped_column(Float, nullable=True)
    pack_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default=ORDERED, nullable=False, index=True)
    ordered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    #: When a person sent it to the supplier (by mail from here, or marked as placed by phone), and who.
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sent_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    expected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: What actually arrived, in `unit`; null until received.
    received_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The invoice line that received it, when the invoice did (no FK: the two modules don't import each other).
    invoice_line_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The approved proposal it came from, if any.
    action_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Who placed it and who received it: an email, or the agent's key.
    placed_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    received_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)

    item: Mapped[InventoryItem | None] = relationship()
    supplier: Mapped[Supplier | None] = relationship()
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan",
                                                            order_by="PurchaseOrderLine.position")


class PurchaseOrderLine(Base):
    """One line of a drafted order: an item (as the invoices name it), how many packs, at what expected price."""

    __tablename__ = "po_lines"
    __table_args__ = (Index("ix_po_lines_restaurant_order", "restaurant_id", "po_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(ForeignKey("restaurants.id", name="fk_po_lines_restaurant"), nullable=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id", name="fk_po_lines_order", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The price item (what the invoices call it) and, when it is counted, the stock item.
    item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inventory_item_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("inventory_items.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Packs ordered, and the pack's name; `units` is the quantity in the item's unit.
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    units: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_price_expected: Mapped[float | None] = mapped_column(Float, nullable=True)
    received_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    invoice_line_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class InventoryAdjustment(Base):
    """One change to an item's stock: how much, why, by whom, and when.

    Written for every change to `quantity_on_hand`, whatever made it -- a
    delivery logged on the Inventory page, an order drawing ingredients
    down, an approved agent reorder, a revert -- by the listener in
    app/restaurant/inventory.py. So the history always adds up to the
    number on hand.
    """

    __tablename__ = "inventory_adjustments"
    # A retried request -- a double tap, a resend after the connection
    # dropped -- carries the same request id, and can't be recorded twice.
    __table_args__ = (UniqueConstraint("restaurant_id", "request_id", name="uq_inventory_adjustments_request"),)

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
    #: The client's Idempotency-Key for a hand-recorded change; null otherwise.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False, index=True)

    # Also what makes a flush insert a new item before its first adjustment.
    item: Mapped[InventoryItem] = relationship()


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_menu_items_restaurant_id_restaurants"), nullable=True, index=True
    )
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
    __table_args__ = (UniqueConstraint("restaurant_id", "request_id", name="uq_orders_request"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_orders_restaurant_id_restaurants"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)  # open|completed|cancelled
    channel: Mapped[str] = mapped_column(String(32), default="dine_in", nullable=False)  # dine_in|takeout|delivery
    table_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    #: The client's Idempotency-Key, so a resent sale isn't rung up twice.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    """An employee's HR profile. See app/hr/staff.py for who may see what.

    `email` is also how a person's login finds their profile: an account
    with the same address, at the same restaurant, is that employee.
    """

    __tablename__ = "staff"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_staff_restaurant_id_restaurants"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Free text, e.g. "Dana Rivera (sister) +1 206 555 0100".
    emergency_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Managers only; never shown to the employee.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[str | None] = mapped_column(
        ForeignKey("restaurants.id", name="fk_shifts_restaurant_id_restaurants"), nullable=True, index=True
    )
    staff_id: Mapped[str] = mapped_column(String(32), ForeignKey("staff.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    #: None while it's a draft only managers see; set when a manager publishes.
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

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

    One per owner who signs up; everything else in the app belongs to one.
    Created with the owner's account, holding only a name, so every column
    but the bookkeeping is nullable: a half-finished setup is a real,
    resumable state. `completed_steps` records which sections have
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
    #: {"<agent key>": {"schedule_cron": "0 10 * * tue", "enabled": true}}: this
    #: restaurant's own schedule and pause switch for the agent. A key that is
    #: missing falls back to the agent definition's, which every restaurant shares.
    agent_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    completed_steps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    setup_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    #: When anything in the profile was last saved, and by whom (a users.id).
    #: No foreign key for `updated_by`: users already points here, and a
    #: cycle between the two tables would complicate every create and drop.
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
