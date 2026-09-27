from datetime import date, datetime, time
from datetime import date as _date, time as _time

from pydantic import BaseModel, ConfigDict


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    contact_info: str | None
    lead_time_days: int


class SupplierCreate(BaseModel):
    name: str
    contact_info: str | None = None
    lead_time_days: int = 3


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


class InventoryItemCreate(BaseModel):
    name: str
    unit: str
    quantity_on_hand: float = 0
    reorder_threshold: float = 0
    reorder_qty: float = 0
    unit_cost: float = 0
    supplier_id: str | None = None


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
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    role: str
    email: str | None


class StaffCreate(BaseModel):
    name: str
    role: str
    email: str | None = None


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    staff_id: str
    date: date
    start_time: time
    end_time: time
    role: str


class ShiftCreate(BaseModel):
    staff_id: str
    date: date
    start_time: time
    end_time: time
    role: str


class StaffUpdate(BaseModel):
    """Partial update. Unset fields are left alone, so a caller can send
    only what changed rather than having to echo the whole record."""

    name: str | None = None
    role: str | None = None
    email: str | None = None


class ShiftUpdate(BaseModel):
    # Aliased types: a field named `date` shadows the `date` class inside
    # this body, so the annotation cannot refer to it by that name.
    staff_id: str | None = None
    date: _date | None = None
    start_time: _time | None = None
    end_time: _time | None = None
    role: str | None = None


class MenuItemUpdate(BaseModel):
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
    name: str | None = None
    contact_info: str | None = None
    lead_time_days: int | None = None


class InventoryItemUpdate(BaseModel):
    name: str | None = None
    unit: str | None = None
    quantity_on_hand: float | None = None
    reorder_threshold: float | None = None
    reorder_qty: float | None = None
    unit_cost: float | None = None
    supplier_id: str | None = None


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
