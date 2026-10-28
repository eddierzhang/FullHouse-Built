from datetime import date, datetime, time
from datetime import date as _date, time as _time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    contact_info: str | None
    lead_time_days: int


class SupplierCreate(BaseModel):
    name: str
    contact_info: str | None = None
    #: Unset takes the owner's default from Restaurant Settings.
    lead_time_days: int | None = None


class InventoryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    unit: str
    quantity_on_hand: float
    reorder_threshold: float
    reorder_qty: float
    unit_cost: float
    supplier_id: str | None
    #: Bought in packs: one `pack_unit` holds `pack_size` of `unit`; both null otherwise.
    pack_unit: str | None = None
    pack_size: float | None = None
    updated_at: datetime

    @computed_field
    @property
    def low_stock(self) -> bool:
        """At or below the reorder point: the same test the dashboard and agent use."""
        return self.quantity_on_hand <= self.reorder_threshold


#: Largest stock figure accepted; far past any kitchen, well short of float trouble.
MAX_QUANTITY = 1_000_000


def _amount(**kwargs):
    # allow_inf_nan=False: JSON can carry NaN and Infinity, and neither is stock.
    return Field(ge=0, le=MAX_QUANTITY, allow_inf_nan=False, **kwargs)


def _item_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter the item's name.")
    return value


def _unit(value: str) -> str:
    from app.restaurant.inventory import normalize_unit

    return normalize_unit(value)


#: Largest pack: a pallet of small things, not a warehouse.
MAX_PACK_SIZE = 100_000


def _pack_unit(value: str | None) -> str | None:
    return None if value in (None, "") else _unit(value)


def check_pack(unit: str | None, pack_unit: str | None, pack_size: float | None) -> None:
    """Both or neither, and a pack can't be counted in the item's own unit."""
    if (pack_unit is None) != (pack_size is None):
        raise ValueError("Give both how it's packed (e.g. case) and how many come in one, or neither.")
    if pack_unit is not None and unit is not None and pack_unit == unit:
        raise ValueError(f"A pack can't be measured in {unit}, the unit stock is counted in.")


class InventoryItemCreate(BaseModel):
    name: str = Field(max_length=255)
    unit: str
    #: What's on hand now; recorded in the item's history as its starting stock.
    quantity_on_hand: float = _amount(default=0)
    reorder_threshold: float = _amount(default=0)
    reorder_qty: float = _amount(default=0)
    unit_cost: float = _amount(default=0)
    supplier_id: str | None = None
    pack_unit: str | None = None
    pack_size: float | None = Field(default=None, gt=0, le=MAX_PACK_SIZE, allow_inf_nan=False)

    _name = field_validator("name")(_item_name)
    _unit = field_validator("unit")(_unit)
    _pack = field_validator("pack_unit")(_pack_unit)

    @model_validator(mode="after")
    def _packs(self) -> "InventoryItemCreate":
        check_pack(self.unit, self.pack_unit, self.pack_size)
        return self


class MenuItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    category: str
    price: float
    cost: float
    is_available: bool
    description: str | None
    regular_price: float | None = None
    promo_ends_at: datetime | None = None


class MenuItemCreate(BaseModel):
    name: str
    category: str
    price: float
    cost: float = 0
    is_available: bool = True
    description: str | None = None


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    menu_item_id: str
    qty: int
    unit_price: float
    notes: str | None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    channel: str
    table_number: int | None
    total: float
    created_at: datetime
    items: list[OrderItemOut]


class OrderItemCreate(BaseModel):
    menu_item_id: str
    qty: int = 1
    notes: str | None = None


class OrderCreate(BaseModel):
    channel: str = "dine_in"
    table_number: int | None = None
    items: list[OrderItemCreate]


class StaffOut(BaseModel):
    """An employee as the viewer may see them (app/hr/staff.py): coworkers
    get a name and role; contact details are for managers and the person
    themselves; notes and `has_account` are for managers only."""

    id: str
    name: str
    role: str
    #: This profile is the signed-in person's own.
    is_me: bool = False
    email: str | None = None
    phone: str | None = None
    emergency_contact: str | None = None
    hire_date: date | None = None
    notes: str | None = None
    #: Someone can sign in with this profile's email. Managers only.
    has_account: bool | None = None


class StaffCreate(BaseModel):
    name: str = Field(max_length=255)
    role: str = Field(max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    emergency_contact: str | None = Field(default=None, max_length=200)
    hire_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    staff_id: str
    date: date
    start_time: time
    end_time: time
    role: str
    #: None while it's a draft, which only managers see.
    published_at: datetime | None = None


class PublishOut(BaseModel):
    published: int


class ShiftCreate(BaseModel):
    staff_id: str
    date: date
    start_time: time
    end_time: time
    role: str


class StaffUpdate(BaseModel):
    """Partial update. Unset fields are left alone, so a caller can send
    only what changed rather than having to echo the whole record."""

    name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    emergency_contact: str | None = Field(default=None, max_length=200)
    hire_date: _date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ShiftUpdate(BaseModel):
    # Aliased types: a field named `date` shadows the `date` class inside
    # this body, so the annotation cannot refer to it by that name.
    staff_id: str | None = None
    date: _date | None = None
    start_time: _time | None = None
    end_time: _time | None = None
    role: str | None = None


class MenuItemUpdate(BaseModel):
    #: The values the editor's form started from, for the fields it changes;
    #: a save is refused if any has changed since (app/restaurant/conflicts.py).
    expected: dict[str, Any] | None = None
    name: str | None = None
    category: str | None = None
    price: float | None = None
    cost: float | None = None
    description: str | None = None
    is_available: bool | None = None


class MenuPerformanceOut(BaseModel):
    id: str
    name: str
    category: str
    price: float
    cost: float
    #: "recipe" when computed from ingredients, "manual" when typed in.
    cost_source: str = "manual"
    description: str | None
    is_available: bool
    units_sold: int
    revenue: float
    margin: float
    margin_pct: float
    profit: float
    regular_price: float | None = None
    promo_ends_at: datetime | None = None


class DailyProfitOut(BaseModel):
    date: str
    revenue: float
    cogs: float
    profit: float
    orders: int


class CategoryProfitOut(BaseModel):
    category: str
    revenue: float
    profit: float
    units: int


class ChannelProfitOut(BaseModel):
    channel: str
    revenue: float
    orders: int


class ProfitSummaryOut(BaseModel):
    days: int
    start: str
    end: str
    revenue: float
    cogs: float
    gross_profit: float
    margin_pct: float
    orders: int
    average_order_value: float
    inventory_value: float
    daily: list[DailyProfitOut]
    by_category: list[CategoryProfitOut]
    by_channel: list[ChannelProfitOut]


class SupplierUpdate(BaseModel):
    #: The values the editor's form started from, for the fields it changes;
    #: a save is refused if any has changed since (app/restaurant/conflicts.py).
    expected: dict[str, Any] | None = None
    name: str | None = None
    contact_info: str | None = None
    lead_time_days: int | None = None


class InventoryItemUpdate(BaseModel):
    """An item's details. Stock isn't one: it changes only through a recorded stock change."""

    #: The values the editor's form started from, for the fields it changes;
    #: a save is refused if any has changed since (app/restaurant/conflicts.py).
    expected: dict[str, Any] | None = None

    name: str | None = Field(default=None, max_length=255)
    unit: str | None = None
    quantity_on_hand: float | None = None
    reorder_threshold: float | None = _amount(default=None)
    reorder_qty: float | None = _amount(default=None)
    unit_cost: float | None = _amount(default=None)
    supplier_id: str | None = None
    #: Set both to buy in packs; set both to null to stop.
    pack_unit: str | None = None
    pack_size: float | None = Field(default=None, gt=0, le=MAX_PACK_SIZE, allow_inf_nan=False)

    _pack = field_validator("pack_unit")(_pack_unit)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _item_name(value)

    @field_validator("unit")
    @classmethod
    def _unit(cls, value: str | None) -> str | None:
        return None if value is None else _unit(value)

    @field_validator("quantity_on_hand")
    @classmethod
    def _no_silent_stock_edits(cls, value: float | None) -> float | None:
        if value is not None:
            raise ValueError(
                "Stock can't be overwritten. Record a delivery, use, waste or stock count so the change is in its history."
            )
        return value


class StockChangeIn(BaseModel):
    reason: Literal["delivery", "used", "waste", "count"]
    #: How much came in or went out; for a stock count, the level counted.
    quantity: float = _amount()
    #: `quantity` is in the item's packs (cases, boxes) rather than its unit.
    in_packs: bool = False
    note: str | None = Field(default=None, max_length=255)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @model_validator(mode="after")
    def _something_changes(self) -> "StockChangeIn":
        if self.reason != "count" and self.quantity == 0:
            raise ValueError("Enter an amount greater than zero.")
        return self


class StockAdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    inventory_item_id: str
    item_name: str
    unit: str
    change: float
    quantity_after: float
    reason: str
    reason_label: str
    note: str | None
    actor_type: str
    actor_id: str | None
    #: The person's name, for changes a signed-in person made.
    actor_name: str | None
    created_at: datetime


class StockChangeOut(BaseModel):
    item: InventoryItemOut
    #: Null when a stock count matched what was already on hand.
    adjustment: StockAdjustmentOut | None


class PurchaseOrderLineOut(BaseModel):
    id: str
    item_id: str | None
    inventory_item_id: str | None
    description: str
    qty: float
    unit: str
    units: float | None
    unit_price_expected: float | None
    received_qty: float | None
    invoice_line_id: str | None


class PurchaseOrderOut(BaseModel):
    """An order with a supplier, wherever it is between drafting and receipt."""

    id: str
    lines: list[PurchaseOrderLineOut] = []
    sent_at: datetime | None = None
    sent_by: str | None = None
    rep_email: str | None = None
    #: Empty for an order with lines.
    inventory_item_id: str | None
    item_name: str
    unit: str
    quantity: float
    packs: float | None
    pack_unit: str | None
    supplier_id: str | None
    supplier_name: str | None
    estimated_cost: float | None
    status: Literal["draft", "ordered", "shipped", "received", "cancelled"]
    ordered_at: datetime
    expected_on: date | None
    shipped_at: datetime | None
    received_at: datetime | None
    received_qty: float | None
    #: Past its expected date and still not here.
    overdue: bool
    #: Received by the invoice for it arriving, rather than by hand.
    received_by_invoice: bool
    action_id: str | None
    note: str | None
    placed_by: str | None
    received_by: str | None


class PurchaseOrderCreate(BaseModel):
    """An order placed outside Recommendations: by phone, email or a supplier's portal."""

    inventory_item_id: str
    supplier_id: str | None = None
    #: In the item's unit, or in its packs with `in_packs`.
    quantity: float = Field(gt=0, le=MAX_QUANTITY, allow_inf_nan=False)
    in_packs: bool = False
    expected_on: date | None = None
    note: str | None = Field(default=None, max_length=255)


class OrderShippedIn(BaseModel):
    expected_on: date | None = None


class OrderSendIn(BaseModel):
    """Who to mail it to; unset means the supplier's remembered rep."""

    to: str | None = Field(default=None, max_length=254)


class OrderReceiveIn(BaseModel):
    """What arrived, in the item's unit; unset means all of it."""

    quantity: float | None = Field(default=None, ge=0, le=MAX_QUANTITY, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=255)


class OrderReceivedOut(BaseModel):
    order: PurchaseOrderOut
    item: InventoryItemOut


class SupplierExposureOut(BaseModel):
    id: str
    name: str
    contact_info: str | None
    lead_time_days: int
    item_count: int
    stock_value: float
    low_stock_count: int
    restock_cost: float
    share_pct: float


class LowStockOut(BaseModel):
    id: str
    name: str
    unit: str
    quantity_on_hand: float
    reorder_threshold: float
    reorder_qty: float
    unit_cost: float
    supplier_id: str | None
    supplier_name: str | None
    lead_time_days: int | None
    restock_cost: float


class CoverOut(BaseModel):
    id: str
    name: str
    unit: str
    quantity_on_hand: float
    daily_use: float | None
    days_of_cover: float | None
    lead_time_days: int | None
    at_risk: bool


class RecipeLineOut(BaseModel):
    inventory_item_id: str
    name: str
    unit: str
    quantity: float
    unit_cost: float
    line_cost: float


class RecipeOut(BaseModel):
    menu_item_id: str
    menu_item_name: str
    price: float
    ingredient_cost: float
    margin_pct: float
    lines: list[RecipeLineOut]


class RecipeLineIn(BaseModel):
    inventory_item_id: str
    quantity: float


class RecipeIn(BaseModel):
    lines: list[RecipeLineIn]


class SupplyChainOut(BaseModel):
    total_stock_value: float
    supplier_count: int
    item_count: int
    unassigned_item_count: int
    unassigned_stock_value: float
    low_stock_count: int
    restock_cost: float
    longest_lead_days: int
    at_risk_count: int
    suppliers: list[SupplierExposureOut]
    low_stock: list[LowStockOut]
    cover: list[CoverOut]


class ForecastDayOut(BaseModel):
    date: str
    revenue: float
    profit: float
    low: float
    high: float


class ForecastOut(BaseModel):
    method: str  # weekday | average | none
    basis_days: int
    confidence: str  # high | medium | low | none
    message: str
    history: list[DailyProfitOut]
    days: list[ForecastDayOut]
    total_revenue: float
    total_profit: float


class InventoryImportIn(BaseModel):
    """A spreadsheet of stock, as CSV text (the browser reads the file)."""

    csv: str = Field(max_length=1_000_000)
    #: Items already tracked: update their details and count, rather than skip them.
    update_existing: bool = False
    #: Check and preview only; nothing is saved.
    dry_run: bool = True


class ImportRowOut(BaseModel):
    line: int
    name: str | None
    #: new | update | skip | error
    status: str
    messages: list[str]
    unit: str | None = None
    quantity_on_hand: float | None = None
    supplier: str | None = None


class InventoryImportOut(BaseModel):
    rows: list[ImportRowOut]
    new: int
    updated: int
    skipped: int
    errors: int
    #: Suppliers named in the file that don't exist yet, and will be added.
    new_suppliers: list[str]
    #: True when the import was carried out (not a dry run, and no errors).
    saved: bool
