"""Stock that tracks itself: count sheets, waste, the variance report, pars and sales depletion."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.platform import flags
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/stock", tags=["stock"])


def _scope(db: Session, user: User) -> str:
    """The signed-in user's restaurant, with this feature switched on for it."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.enabled(db, restaurant_id, flags.V3_STOCK):
        raise HTTPException(404, "Stock tracking isn't switched on for this restaurant (Profile, Features).")
    return restaurant_id


from datetime import date, datetime  # noqa: E402

from fastapi import Request  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.restaurant import inventory  # noqa: E402
from app.restaurant.conflicts import idempotency_key  # noqa: E402
from app.restaurant.models import InventoryItem  # noqa: E402
from app.stock import engine  # noqa: E402
from app.stock.models import OPEN, StockCount, StorageArea  # noqa: E402


def _item(db: Session, restaurant_id: str, item_id: str, lock: bool = False) -> InventoryItem:
    if lock:
        inventory.lock_items(db, [item_id])
    query = db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id, InventoryItem.id == item_id)
    item = (query.populate_existing() if lock else query).first()
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    return item


def _area(db: Session, restaurant_id: str, area_id: str) -> StorageArea:
    area = db.query(StorageArea).filter(StorageArea.restaurant_id == restaurant_id, StorageArea.id == area_id).first()
    if area is None:
        raise HTTPException(404, "Storage area not found")
    return area


def _count(db: Session, restaurant_id: str, count_id: str) -> StockCount:
    count = db.query(StockCount).filter(StockCount.restaurant_id == restaurant_id, StockCount.id == count_id).first()
    if count is None:
        raise HTTPException(404, "Count not found")
    return count


# --- where things are kept ----------------------------------------------------------------------


class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class AreaOut(BaseModel):
    id: str
    name: str
    position: int
    items: int


class PlaceIn(BaseModel):
    #: Null takes the item out of every area.
    storage_area_id: str | None = None
    shelf_order: int | None = Field(default=None, ge=0, le=100000)
    #: Usable share of what is bought, 1-100; null means all of it.
    yield_pct: float | None = Field(default=None, gt=0, le=100)


def _areas_out(db: Session, restaurant_id: str) -> list[AreaOut]:
    held: dict[str | None, int] = {}
    for (area_id,) in db.query(InventoryItem.storage_area_id).filter(InventoryItem.restaurant_id == restaurant_id):
        held[area_id] = held.get(area_id, 0) + 1
    return [AreaOut(id=a.id, name=a.name, position=a.position, items=held.get(a.id, 0)) for a in engine.areas(db, restaurant_id)]


@router.get("/areas", response_model=list[AreaOut])
def list_areas(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _areas_out(db, _scope(db, user))


@router.post("/areas", response_model=list[AreaOut], status_code=201)
def add_area(body: AreaIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    restaurant_id = _scope(db, user)
    name = body.name.strip()
    if any(a.name.lower() == name.lower() for a in engine.areas(db, restaurant_id)):
        raise HTTPException(409, f"There is already an area called {name}.")
    db.add(StorageArea(restaurant_id=restaurant_id, name=name, position=len(engine.areas(db, restaurant_id))))
    db.commit()
    return _areas_out(db, restaurant_id)


@router.delete("/areas/{area_id}", response_model=list[AreaOut])
def remove_area(area_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Remove an area. Its items stay in stock and go back to "not placed"."""
    restaurant_id = _scope(db, user)
    area = _area(db, restaurant_id, area_id)
    for item in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id, InventoryItem.storage_area_id == area.id):
        item.storage_area_id = None
    db.delete(area)
    db.commit()
    return _areas_out(db, restaurant_id)


@router.put("/items/{item_id}/place")
def place_item(item_id: str, body: PlaceIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Say where an item is kept, its place on the shelf, and how much of it is usable."""
    restaurant_id = _scope(db, user)
    item = _item(db, restaurant_id, item_id)
    fields = body.model_fields_set
    if "storage_area_id" in fields:
        item.storage_area_id = _area(db, restaurant_id, body.storage_area_id).id if body.storage_area_id else None
    if "shelf_order" in fields and body.shelf_order is not None:
        item.shelf_order = body.shelf_order
    if "yield_pct" in fields:
        item.yield_pct = body.yield_pct
    db.commit()
    return {"id": item.id, "storage_area_id": item.storage_area_id, "shelf_order": item.shelf_order, "yield_pct": item.yield_pct}


# --- counts -------------------------------------------------------------------------------------


class CountStartIn(BaseModel):
    #: An area's id, "unplaced" for items in no area, or null for everything.
    storage_area_id: str | None = None
    client_key: str | None = Field(default=None, max_length=64)


class CountLineIn(BaseModel):
    inventory_item_id: str
    counted_qty: float | None = Field(default=None, ge=0, le=1_000_000)


class CountLinesIn(BaseModel):
    lines: list[CountLineIn] = Field(max_length=2000)


class CountNowIn(CountLinesIn):
    storage_area_id: str | None = None
    client_key: str = Field(min_length=8, max_length=64)


class CountLineOut(BaseModel):
    inventory_item_id: str
    item_name: str
    unit: str
    expected_qty: float
    counted_qty: float | None
    unit_cost: float
    pack_size: float | None = None
    pack_unit: str | None = None


class CountOut(BaseModel):
    id: str
    storage_area_id: str | None
    area_name: str
    status: str
    started_by: str | None
    started_at: datetime
    posted_at: datetime | None
    lines: list[CountLineOut]
    summary: dict


def _area_name(db: Session, restaurant_id: str, area_id: str | None) -> str:
    if area_id is None:
        return "Everything"
    if area_id == "unplaced":
        return "Not placed yet"
    area = db.query(StorageArea).filter(StorageArea.restaurant_id == restaurant_id, StorageArea.id == area_id).first()
    return area.name if area else "A removed area"


def _count_out(db: Session, count: StockCount, with_lines: bool = True) -> CountOut:
    packs = {i.id: (i.pack_size, i.pack_unit) for i in db.query(InventoryItem).filter(InventoryItem.restaurant_id == count.restaurant_id)}
    lines = [CountLineOut(inventory_item_id=l.inventory_item_id, item_name=l.item_name, unit=l.unit, expected_qty=l.expected_qty,
                          counted_qty=l.counted_qty, unit_cost=l.unit_cost, pack_size=packs.get(l.inventory_item_id, (None, None))[0],
                          pack_unit=packs.get(l.inventory_item_id, (None, None))[1])
             for l in engine.count_lines(db, count)] if with_lines else []
    return CountOut(id=count.id, storage_area_id=count.storage_area_id, area_name=_area_name(db, count.restaurant_id, count.storage_area_id),
                    status=count.status, started_by=count.started_by, started_at=count.started_at, posted_at=count.posted_at, lines=lines,
                    summary=engine.summary_of(db, count))


@router.get("/counts", response_model=list[CountOut])
def list_counts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Open counts first, then the last ten posted, without their lines."""
    restaurant_id = _scope(db, user)
    rows = (db.query(StockCount).filter(StockCount.restaurant_id == restaurant_id)
            .order_by((StockCount.status != OPEN), StockCount.started_at.desc()).limit(12).all())
    return [_count_out(db, c, with_lines=False) for c in rows]


@router.post("/counts", response_model=CountOut, status_code=201)
def start_count(body: CountStartIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Open a count sheet for an area, or for everything. Staff count; an open sheet for the same area is carried on."""
    restaurant_id = _scope(db, user)
    if body.storage_area_id and body.storage_area_id != "unplaced":
        _area(db, restaurant_id, body.storage_area_id)
    count = engine.start_count(db, restaurant_id, body.storage_area_id, user.email, body.client_key)
    db.commit()
    return _count_out(db, count)


@router.post("/counts/now", response_model=CountOut, status_code=201)
def count_now(body: CountNowIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A whole count in one request, posted at once: a phone catching up after poor signal. The same client_key posts once."""
    restaurant_id = _scope(db, user)
    try:
        count, _ = engine.count_now(db, restaurant_id, [l.model_dump() for l in body.lines], storage_area_id=body.storage_area_id,
                                    started_by=user.email, client_key=body.client_key, area_name=_area_name(db, restaurant_id, body.storage_area_id))
    except ValueError as err:
        db.rollback()
        raise HTTPException(422, str(err)) from err
    db.commit()
    return _count_out(db, count)


@router.get("/counts/{count_id}", response_model=CountOut)
def read_count(count_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _count_out(db, _count(db, _scope(db, user), count_id))


@router.put("/counts/{count_id}/lines", response_model=CountOut)
def save_count_lines(count_id: str, body: CountLinesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Save what has been counted so far. Nothing reaches the stock figures until the count is posted."""
    count = _count(db, _scope(db, user), count_id)
    try:
        engine.save_lines(db, count, [l.model_dump() for l in body.lines])
    except ValueError as err:
        db.rollback()
        raise HTTPException(409, str(err)) from err
    db.commit()
    return _count_out(db, count)


@router.post("/counts/{count_id}/post", response_model=CountOut)
def post_count(count_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Make the count the stock figures. Lines nobody counted are left as they were; posting twice is once."""
    restaurant_id = _scope(db, user)
    count = _count(db, restaurant_id, count_id)
    inventory.lock_items(db, [l.inventory_item_id for l in engine.count_lines(db, count)])
    engine.post_count(db, count, None if count.storage_area_id in (None, "unplaced") else _area_name(db, restaurant_id, count.storage_area_id))
    engine.rollup(db, restaurant_id)
    db.commit()
    return _count_out(db, count)


@router.delete("/counts/{count_id}")
def discard_count(count_id: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Throw an open count away. A posted count is history and stays; undo it from History."""
    count = _count(db, _scope(db, user), count_id)
    if count.status != OPEN:
        raise HTTPException(409, "That count has been posted. Undo its changes from History instead.")
    db.delete(count)
    db.commit()
    return {"discarded": count_id}


# --- waste --------------------------------------------------------------------------------------


class WasteIn(BaseModel):
    inventory_item_id: str
    quantity: float = Field(gt=0, le=1_000_000)
    reason: str
    note: str | None = Field(default=None, max_length=160)


@router.post("/waste")
def record_waste(body: WasteIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Something thrown away, and why. Staff record it. Sent twice with the same Idempotency-Key it is recorded once."""
    restaurant_id = _scope(db, user)
    if body.reason not in engine.WASTE_REASONS:
        raise HTTPException(422, f"Pick a reason: {', '.join(engine.WASTE_REASONS.values())}.")
    key = idempotency_key(request)
    item = _item(db, restaurant_id, body.inventory_item_id, lock=True)
    taken = engine.record_waste(db, item, body.quantity, body.reason, body.note, source_key=f"waste:{key}" if key else None)
    engine.rollup(db, restaurant_id, days=3)
    db.commit()
    return {"inventory_item_id": item.id, "name": item.name, "unit": item.unit, "taken": taken, "on_hand": item.quantity_on_hand,
            "dollars": round(taken * item.unit_cost, 2)}


@router.get("/waste")
def read_waste(days: int = 28, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"reasons": engine.WASTE_REASONS, "entries": engine.waste_log(db, _scope(db, user), min(max(days, 1), 90))}


# --- the report ---------------------------------------------------------------------------------


@router.get("/variance")
def read_variance(days: int = 28, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Where stock went: received, used by sales, wasted, and what counts found missing or extra."""
    if days not in (7, 28, 56):
        raise HTTPException(422, "days must be 7, 28 or 56.")
    return engine.variance(db, _scope(db, user), days)


@router.get("/status")
def read_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Whether sales are coming off the shelf, what needs a decision today, and POS items that match no recipe."""
    restaurant_id = _scope(db, user)
    return {**engine.status(db, restaurant_id), "alerts": engine.alerts(db, restaurant_id), "unmapped": engine.unmapped(db, restaurant_id),
            "needs_conversion": engine.needs_conversion(db, restaurant_id), "conversions": engine.conversions(db, restaurant_id),
            "pos_maps": [{"pos_item_name": m.pos_item_name, "menu_item_id": m.menu_item_id, "lines": m.lines, "ignore": m.ignore}
                         for m in engine.pos_maps(db, restaurant_id).values()]}


class SourceIn(BaseModel):
    #: "pos", "app", or null to decide by whether POS sales exist.
    sales_source: str | None = Field(default=None, pattern="^(pos|app)$")


class ConversionIn(BaseModel):
    #: The other unit ("lb"), and how many of the item's own unit one of it is; null factor forgets it.
    other_unit: str = Field(min_length=1, max_length=32)
    factor: float | None = Field(default=None, gt=0, le=100_000)


class PosLineIn(BaseModel):
    inventory_item_id: str
    quantity: float = Field(gt=0, le=10_000)


class PosMapIn(BaseModel):
    pos_item_name: str = Field(min_length=1, max_length=200)
    #: Exactly one of: a dish it is another name for, ingredients one sold uses, or ignore.
    menu_item_id: str | None = None
    lines: list[PosLineIn] = Field(default_factory=list, max_length=20)
    ignore: bool = False


@router.put("/settings")
def set_settings(body: SourceIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Where sales take stock off from: the point of sale, or orders taken in FullHouse. Never both."""
    restaurant_id = _scope(db, user)
    source = engine.set_sales_source(db, restaurant_id, body.sales_source)
    db.commit()
    return {"sales_source": source, "sales_source_pinned": body.sales_source is not None}


@router.put("/items/{item_id}/conversion")
def set_conversion(item_id: str, body: ConversionIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Say how a unit an invoice uses relates to the unit the item is counted in: 1 lb of basil = 8 bunches."""
    restaurant_id = _scope(db, user)
    item = _item(db, restaurant_id, item_id)
    if body.other_unit.strip().lower() == item.unit.strip().lower():
        raise HTTPException(422, f"{item.name} is already counted in {item.unit}.")
    engine.set_conversion(db, item, body.other_unit, body.factor)
    db.commit()
    return {"needs_conversion": engine.needs_conversion(db, restaurant_id), "conversions": engine.conversions(db, restaurant_id)}


@router.put("/pos-items")
def map_pos_item(body: PosMapIn, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Say what a POS item means for stock: another name for a dish, a modifier that uses ingredients, or nothing."""
    from app.restaurant.models import MenuItem
    from app.stock.models import PosItemMap

    restaurant_id = _scope(db, user)
    chosen = sum([bool(body.menu_item_id), bool(body.lines), body.ignore])
    if chosen != 1:
        raise HTTPException(422, "Pick one: a dish, the ingredients it uses, or ignore.")
    if body.menu_item_id and db.query(MenuItem.id).filter(MenuItem.restaurant_id == restaurant_id, MenuItem.id == body.menu_item_id).first() is None:
        raise HTTPException(404, "Dish not found")
    for line in body.lines:
        _item(db, restaurant_id, line.inventory_item_id)
    key = body.pos_item_name.strip().lower()
    row = db.query(PosItemMap).filter(PosItemMap.restaurant_id == restaurant_id, PosItemMap.pos_key == key).first()
    if row is None:
        row = PosItemMap(restaurant_id=restaurant_id, pos_key=key, pos_item_name=body.pos_item_name.strip())
        db.add(row)
    row.menu_item_id, row.ignore = body.menu_item_id, body.ignore
    row.lines = [l.model_dump() for l in body.lines]
    db.commit()
    return {"unmapped": engine.unmapped(db, restaurant_id)}


@router.delete("/pos-items")
def unmap_pos_item(pos_item_name: str, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    from app.stock.models import PosItemMap

    restaurant_id = _scope(db, user)
    row = db.query(PosItemMap).filter(PosItemMap.restaurant_id == restaurant_id, PosItemMap.pos_key == pos_item_name.strip().lower()).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return {"unmapped": engine.unmapped(db, restaurant_id)}


@router.post("/run")
def run_now(user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Take sales off the shelf now, rather than waiting for the next sync."""
    restaurant_id = _scope(db, user)
    result = engine.run(db, restaurant_id, date.today())
    db.commit()
    return {"moved": result["moved"], "sales": result["sales"], "told": result["told"]}
