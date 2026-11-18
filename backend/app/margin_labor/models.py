"""The shared data of the Margin & Labor Engine (spec section 4): order-level sales, time punches, delivery
payouts, outside demand drivers, and the interval and forecast tables every module reads.

Raw tables are written by idempotent upserts on an external key, so an import or a sync replayed changes
nothing. Derived tables (`interval_actuals`, `forecast_intervals`) are rewritten by jobs and are a pure function
of the raw rows. Money and quantities are `Dec` (numeric(12,4)); forecast values are floats, as the spec allows.
Every table leads its indexes with `restaurant_id`.

Nothing here replaces an existing table: `sales_items`, `sales_days`, `labor_days`, `supplier_calendars` and
the rest keep working as they do.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.db.types import Dec, UTCDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


CHANNELS = ("dine_in", "takeout", "delivery_own", "delivery_platform", "catering")
ORDER_STATUSES = ("open", "closed", "voided", "refunded")
PUNCH_SOURCES = ("pos", "scheduler", "payroll", "csv")
DRIVER_KINDS = ("holiday", "local_event", "weather", "promo", "closure", "reservations")


class _Row:
    """The columns every table here has."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)


def _tenant(table: str) -> Mapped[str]:
    return mapped_column(ForeignKey("restaurants.id", name=f"fk_{table}_restaurant"), nullable=False)


# --- raw ------------------------------------------------------------------------------------------


class PosItem(_Row, Base):
    """The point of sale's menu catalog: what an item is called there and what it sells for."""

    __tablename__ = "pos_items"
    __table_args__ = (UniqueConstraint("restaurant_id", "connection_key", "pos_item_id", name="uq_pos_items_item"),)

    restaurant_id: Mapped[str] = _tenant("pos_items")
    #: The connection the item came from, or "csv" for an import: part of the key, so never null.
    connection_key: Mapped[str] = mapped_column(String(40), default="csv", nullable=False)
    pos_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Dec(), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SalesOrder(_Row, Base):
    """One order as the point of sale recorded it, with when it was opened and on which channel."""

    __tablename__ = "sales_orders"
    __table_args__ = (UniqueConstraint("restaurant_id", "connection_key", "external_id", name="uq_sales_orders_external"),
                      Index("ix_sales_orders_restaurant_date", "restaurant_id", "business_date", "channel"))

    restaurant_id: Mapped[str] = _tenant("sales_orders")
    connection_key: Mapped[str] = mapped_column(String(40), default="csv", nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: The trading day it belongs to (a sale at 01:30 on Saturday is Friday's), set once at ingestion.
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="dine_in", nullable=False)
    platform: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: The POS guest count, when it has one. Never estimated.
    covers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    discounts: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    net: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    tax: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    tips: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="closed", nullable=False)


class SalesOrderLine(_Row, Base):
    __tablename__ = "sales_order_lines"
    __table_args__ = (UniqueConstraint("order_id", "line_no", name="uq_sales_order_lines_line"),
                      Index("ix_sales_order_lines_restaurant_date_item", "restaurant_id", "business_date", "pos_item_id"),
                      Index("ix_sales_order_lines_restaurant_order", "restaurant_id", "order_id"))

    restaurant_id: Mapped[str] = _tenant("sales_order_lines")
    order_id: Mapped[str] = mapped_column(ForeignKey("sales_orders.id", name="fk_sales_order_lines_order", ondelete="CASCADE"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    pos_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    pos_item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    net: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    modifiers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Voided after the kitchen made it: the food was used, so it counts toward expected use.
    voided_after_fire: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class LaborPunch(_Row, Base):
    """One clock-in and clock-out. Read from a POS, a scheduler, payroll or a CSV; never written back."""

    __tablename__ = "labor_punches"
    __table_args__ = (UniqueConstraint("restaurant_id", "source", "external_id", name="uq_labor_punches_external"),
                      Index("ix_labor_punches_restaurant_date_role", "restaurant_id", "business_date", "role_key"))

    restaurant_id: Mapped[str] = _tenant("labor_punches")
    #: The person in `staff`, when the import could match one; the name as the source wrote it, always.
    employee_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_key: Mapped[str] = mapped_column(String(40), nullable=False)
    clock_in: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    clock_out: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(12), default="csv", nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)


class ChannelPayout(_Row, Base):
    """One order on a delivery platform's payout report: what it took and what it paid."""

    __tablename__ = "channel_payouts"
    __table_args__ = (UniqueConstraint("restaurant_id", "platform", "external_order_id", name="uq_channel_payouts_order"),
                      Index("ix_channel_payouts_restaurant_paid", "restaurant_id", "paid_at"))

    restaurant_id: Mapped[str] = _tenant("channel_payouts")
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The sales order it matched, on the platform's order id; null until one is found.
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gross: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    adjustments: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    payout: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class DemandDriver(_Row, Base):
    """Something outside the restaurant that moves demand on a day or an hour: a holiday, an event, rain."""

    __tablename__ = "demand_drivers"
    __table_args__ = (UniqueConstraint("restaurant_id", "date", "slot", "kind", "name", name="uq_demand_drivers_driver"),)

    restaurant_id: Mapped[str] = _tenant("demand_drivers")
    date: Mapped[date] = mapped_column(Date, nullable=False)
    hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The hour, or -1 for the whole day: part of the key, where a null would not be.
    slot: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="owner", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


# --- reference and settings -----------------------------------------------------------------------


class ItemConversion(_Row, Base):
    """How a recipe or pack unit of an item converts to its base unit (g, ml or each): 1 lb = 453.592 g."""

    __tablename__ = "item_conversions"
    __table_args__ = (UniqueConstraint("restaurant_id", "canonical_item_id", "from_unit", name="uq_item_conversions_unit"),)

    restaurant_id: Mapped[str] = _tenant("item_conversions")
    #: A `price_items` row: the item as the invoices know it.
    canonical_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    from_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    to_base_factor: Mapped[Decimal] = mapped_column(Dec(16, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(10), default="user", nullable=False)  # invoice|user|agent
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SupplierSchedule(_Row, Base):
    """One delivery day of a supplier: when the order for it must be in, and the smallest order they take."""

    __tablename__ = "supplier_schedules"
    __table_args__ = (UniqueConstraint("restaurant_id", "supplier_id", "delivery_weekday", name="uq_supplier_schedules_day"),)

    restaurant_id: Mapped[str] = _tenant("supplier_schedules")
    supplier_id: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Weekdays, Monday = 0.
    order_cutoff_weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    order_cutoff_time: Mapped[str] = mapped_column(String(5), default="14:00", nullable=False)
    delivery_weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_order_amount: Mapped[Decimal | None] = mapped_column(Dec(), nullable=True)


class RestaurantSettings(_Row, Base):
    """The settings these modules add, one row per restaurant. The time zone and opening hours stay where
    they are, on `restaurants`. A restaurant with no row has the defaults (`settings.for_restaurant`)."""

    __tablename__ = "restaurant_settings"
    __table_args__ = (UniqueConstraint("restaurant_id", name="uq_restaurant_settings_restaurant"),)

    restaurant_id: Mapped[str] = _tenant("restaurant_settings")
    #: When a trading day starts, "HH:MM" local: a sale before it belongs to the day before.
    business_day_start: Mapped[str] = mapped_column(String(5), default="04:00", nullable=False)
    target_food_cost_pct: Mapped[Decimal] = mapped_column(Dec(6, 4), default=Decimal("0.30"), nullable=False)
    target_labor_pct: Mapped[Decimal | None] = mapped_column(Dec(6, 4), nullable=True)
    #: The cents a menu price ends in: 0.00, 0.50, 0.95, 0.99.
    price_ending: Mapped[Decimal] = mapped_column(Dec(4, 2), default=Decimal("0.00"), nullable=False)
    costing_price_policy: Mapped[str] = mapped_column(String(10), default="latest", nullable=False)  # latest|avg_30d
    #: Thresholds and weights an owner has changed from the spec's starting defaults, by name.
    overrides: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ChannelTerms(_Row, Base):
    """A delivery platform's contract rates, used for an order whose payout row is missing."""

    __tablename__ = "channel_terms"
    __table_args__ = (UniqueConstraint("restaurant_id", "platform", "effective_from", name="uq_channel_terms_from"),)

    restaurant_id: Mapped[str] = _tenant("channel_terms")
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    commission_pct: Mapped[Decimal] = mapped_column(Dec(8, 6), default=Decimal(0), nullable=False)
    processing_pct: Mapped[Decimal] = mapped_column(Dec(8, 6), default=Decimal(0), nullable=False)
    fixed_fee_per_order: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    packaging_cost_per_order: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


# --- derived --------------------------------------------------------------------------------------


class IntervalActual(_Row, Base):
    """What happened in one 15-minute interval on one channel. Rewritten by the `rollup_intervals` job."""

    __tablename__ = "interval_actuals"
    __table_args__ = (UniqueConstraint("restaurant_id", "business_date", "interval_start", "channel", name="uq_interval_actuals_interval"),)

    restaurant_id: Mapped[str] = _tenant("interval_actuals")
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    interval_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Null when the POS gave no guest counts for the interval.
    covers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    net: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


class ForecastRun(_Row, Base):
    """One run of a forecast job: which model, on which inputs, and how it scored."""

    __tablename__ = "forecast_runs"
    __table_args__ = (Index("ix_forecast_runs_restaurant_started", "restaurant_id", "started_at"),)

    restaurant_id: Mapped[str] = _tenant("forecast_runs")
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # weekly|intraday
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="running", nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ForecastInterval(_Row, Base):
    """A forecast for one interval (or one whole day) of one metric: Expected, and the Likely range."""

    __tablename__ = "forecast_intervals"
    __table_args__ = (UniqueConstraint("restaurant_id", "business_date", "interval_start", "metric", "channel", "kind", "grain",
                                       name="uq_forecast_intervals_row"),)

    restaurant_id: Mapped[str] = _tenant("forecast_intervals")
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    interval_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    metric: Mapped[str] = mapped_column(String(8), nullable=False)  # orders|covers|items|net
    #: A channel, or "all": part of the key, where a null would not be.
    channel: Mapped[str] = mapped_column(String(20), default="all", nullable=False)
    p10: Mapped[float] = mapped_column(Float, nullable=False)
    p50: Mapped[float] = mapped_column(Float, nullable=False)
    p90: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), default="weekly", nullable=False)  # weekly|intraday
    grain: Mapped[str] = mapped_column(String(10), default="interval", nullable=False)  # day|interval
    drivers_applied: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Finding(_Row, Base):
    """Something a module found, in dollars a week, that stays open until it is fixed or stops being true.

    The digest's cards for these modules are built from the open rows. A finding stores the inputs behind
    its number (`basis`), so the page can explain it without recomputing.
    """

    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_restaurant_status_type", "restaurant_id", "status", "type"),)

    restaurant_id: Mapped[str] = _tenant("findings")
    type: Mapped[str] = mapped_column(String(24), nullable=False)  # menu_margin|usage_variance|usage_data_gap|channel_margin
    #: What it is about: a recipe, an ingredient, an item on a platform.
    subject_table: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    weekly_dollars: Mapped[Decimal] = mapped_column(Dec(), default=Decimal(0), nullable=False)
    basis: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)  # open|closed|dismissed
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Module A: live recipe costing ---------------------------------------------------------------


class DishCost(_Row, Base):
    """What one recipe cost to make at one moment, with everything used to work it out (`basis`).

    A new row is written whenever an input changes; exactly one row per recipe is current. The older
    `plate_costs` table (one float per recipe per week) is left as it is for the pages that read it.
    """

    __tablename__ = "dish_costs"
    __table_args__ = (
        Index("uq_dish_costs_current", "restaurant_id", "recipe_id", unique=True, sqlite_where=text("is_current"), postgresql_where=text("is_current")),
        Index("ix_dish_costs_restaurant_recipe_at", "restaurant_id", "recipe_id", "effective_at"),
    )

    restaurant_id: Mapped[str] = _tenant("dish_costs")
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id", name="fk_dish_costs_recipe", ondelete="CASCADE"), nullable=False)
    week_of: Mapped[date] = mapped_column(Date, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    batch_cost: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    plate_cost: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    #: Excluding tax. Null when neither the POS nor recent sales give one: the cost then shows with no percentage.
    menu_price: Mapped[Decimal | None] = mapped_column(Dec(), nullable=True)
    food_cost_pct: Mapped[Decimal | None] = mapped_column(Dec(10, 6), nullable=True)
    margin_dollars: Mapped[Decimal | None] = mapped_column(Dec(), nullable=True)
    lines_priced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lines_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(12), nullable=False)
    #: The event (`events.seq`) that caused this recost, when one did.
    trigger_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: A hash of every input: the same inputs never write a second row.
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: {"lines": [...per line: price point, unit price, conversion, yield, cost], "menu_price": {...}, "policy": ...}
    basis: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    unpriced: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


# --- Module B: usage variance without counts -----------------------------------------------------


class UsageVariance(_Row, Base):
    """One ingredient over one 28-day window: what the recipes say was used, what was bought, and the gap left
    after one typical delivery of slack. Rewritten by the `usage_variance` job; a pure function of its inputs."""

    __tablename__ = "usage_variance"
    __table_args__ = (UniqueConstraint("restaurant_id", "canonical_item_id", "window_end", name="uq_usage_variance_window"),)

    restaurant_id: Mapped[str] = _tenant("usage_variance")
    #: A `price_items` row.
    canonical_item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    #: All quantities are in the ingredient's base unit.
    theoretical_qty: Mapped[Decimal] = mapped_column(Dec(14, 4), nullable=False)
    purchased_qty: Mapped[Decimal] = mapped_column(Dec(14, 4), nullable=False)
    noise_qty: Mapped[Decimal] = mapped_column(Dec(14, 4), nullable=False)
    unexplained_qty: Mapped[Decimal] = mapped_column(Dec(14, 4), nullable=False)
    avg_unit_price: Mapped[Decimal] = mapped_column(Dec(16, 8), nullable=False)
    weekly_dollars: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    recipe_coverage: Mapped[Decimal] = mapped_column(Dec(8, 6), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)  # setup|learning|live
    basis: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(12), nullable=False)


# --- Module E: channel profitability -------------------------------------------------------------


class ChannelItemMargin(_Row, Base):
    """What one menu item keeps per unit on one channel (and platform) over a 28-day window, after commission,
    fees, packaging and food cost. Rewritten by the `channel_margins` job."""

    __tablename__ = "channel_item_margins"
    __table_args__ = (UniqueConstraint("restaurant_id", "pos_item_id", "channel", "platform", "window_end", name="uq_channel_item_margins_row"),)

    restaurant_id: Mapped[str] = _tenant("channel_item_margins")
    pos_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    pos_item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The platform's name, or "" for a channel that has none: part of the key, where a null would not be.
    platform: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    units: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    unit_revenue: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    commission_unit: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    processing_unit: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    fixed_unit: Mapped[Decimal] = mapped_column(Dec(), nullable=False)
    #: Null when the item has no fully priced recipe: what it keeps is then not known.
    plate_cost: Mapped[Decimal | None] = mapped_column(Dec(), nullable=True)
    unit_contribution: Mapped[Decimal | None] = mapped_column(Dec(), nullable=True)
    contribution_pct: Mapped[Decimal | None] = mapped_column(Dec(10, 6), nullable=True)
    #: The share of this row's orders whose fees came from a payout report rather than the contract rates.
    payout_match_rate: Mapped[Decimal | None] = mapped_column(Dec(8, 6), nullable=True)
    basis: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(12), nullable=False)
