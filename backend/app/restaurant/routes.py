from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.db.session import get_db
from app.restaurant import crud, inventory, profile, schemas
from app.restaurant.models import (
    InventoryAdjustment,
    InventoryItem,
    MenuItem,
    Order,
    OrderItem,
    RecipeItem,
    Shift,
    Staff,
    Supplier,
)

router = APIRouter(prefix="/api/v1/restaurant", tags=["restaurant"])


@router.get("/menu-items", response_model=list[schemas.MenuItemOut])
def list_menu_items(db: Session = Depends(get_db)):
    return crud.list_menu_items(db)


@router.post("/menu-items", response_model=schemas.MenuItemOut)
def create_menu_item(payload: schemas.MenuItemCreate, db: Session = Depends(get_db)):
    item = MenuItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# --- inventory ------------------------------------------------------------------------
# Every route works on the signed-in user's own restaurant: another
# restaurant's items read as missing. Everyone who works there can see
# stock, add items and record stock changes; changing an item's details
# is for owners and admins.


def _item_or_404(db: Session, user: User, item_id: str, lock: bool = False) -> InventoryItem:
    query = inventory.in_scope(db.query(InventoryItem), inventory.scope_for(db, user)).filter(InventoryItem.id == item_id)
    # Locked for a stock change, so two people logging at once can't lose one's change.
    item = (query.with_for_update() if lock else query).first()
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    return item


def _name_taken(db: Session, restaurant_id: str | None, name: str, except_id: str | None = None) -> bool:
    query = inventory.in_scope(db.query(InventoryItem), restaurant_id).filter(
        func.lower(InventoryItem.name) == name.lower()
    )
    if except_id:
        query = query.filter(InventoryItem.id != except_id)
    return db.query(query.exists()).scalar()


def _adjustments_out(db: Session, rows: list[InventoryAdjustment]) -> list[schemas.StockAdjustmentOut]:
    items = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.id.in_({r.inventory_item_id for r in rows}))}
    emails = {r.actor_id for r in rows if r.actor_type == "human" and r.actor_id}
    names = {u.email: u.name for u in db.query(User).filter(User.email.in_(emails))} if emails else {}
    return [
        schemas.StockAdjustmentOut(
            id=r.id,
            inventory_item_id=r.inventory_item_id,
            item_name=items[r.inventory_item_id].name,
            unit=items[r.inventory_item_id].unit,
            change=r.change,
            quantity_after=r.quantity_after,
            reason=r.reason,
            reason_label=inventory.REASONS.get(r.reason, r.reason),
            note=r.note,
            actor_type=r.actor_type,
            actor_id=r.actor_id,
            actor_name=names.get(r.actor_id) if r.actor_type == "human" else None,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/inventory-items", response_model=list[schemas.InventoryItemOut])
def list_inventory_items(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """This restaurant's stock, as the database has it now."""
    return inventory.items_in(db, inventory.scope_for(db, user))


@router.post("/inventory-items", response_model=schemas.InventoryItemOut, status_code=201)
def create_inventory_item(
    payload: schemas.InventoryItemCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Start tracking an item. Its opening stock is the first entry in its history."""
    restaurant_id = inventory.scope_for(db, user)
    if _name_taken(db, restaurant_id, payload.name):
        raise HTTPException(409, f"{payload.name} is already tracked. Record a stock change on it instead.")
    if payload.supplier_id and not db.get(Supplier, payload.supplier_id):
        raise HTTPException(404, "Supplier not found")
    item = InventoryItem(**payload.model_dump(), restaurant_id=restaurant_id)
    with inventory.stock_change("initial"):
        db.add(item)
        db.commit()
    db.refresh(item)
    return item


@router.post("/inventory-items/{inventory_item_id}/adjustments", response_model=schemas.StockChangeOut)
def record_stock_change(
    inventory_item_id: str,
    payload: schemas.StockChangeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """A delivery, stock used or wasted, or a count. Recorded with who and why."""
    item = _item_or_404(db, user, inventory_item_id, lock=True)
    before = item.quantity_on_hand
    if payload.reason == "count":
        after = payload.quantity
    elif payload.reason == "delivery":
        after = before + payload.quantity
    else:
        after = before - payload.quantity
    after = round(after, 6)
    if after < 0:
        db.rollback()  # release the row lock
        raise HTTPException(
            422,
            f"Only {before:g} {item.unit} of {item.name} is on hand, so {payload.quantity:g} can't have been "
            f"{'used' if payload.reason == 'used' else 'wasted'}. Check the amount, or record a stock count.",
        )
    if after > schemas.MAX_QUANTITY:
        db.rollback()
        raise HTTPException(422, f"That would put {item.name} over {schemas.MAX_QUANTITY:,} {item.unit}. Check the amount.")
    if after == before:
        db.rollback()
        return schemas.StockChangeOut(item=schemas.InventoryItemOut.model_validate(item), adjustment=None)

    with inventory.stock_change(payload.reason, payload.note):
        item.quantity_on_hand = after
        db.commit()
    db.refresh(item)
    latest = (
        db.query(InventoryAdjustment)
        .filter(InventoryAdjustment.inventory_item_id == item.id)
        .order_by(InventoryAdjustment.id.desc())
        .first()
    )
    return schemas.StockChangeOut(
        item=schemas.InventoryItemOut.model_validate(item), adjustment=_adjustments_out(db, [latest])[0]
    )


@router.get("/inventory-adjustments", response_model=list[schemas.StockAdjustmentOut])
def list_stock_changes(
    inventory_item_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """This restaurant's stock history, newest first; one item's with `inventory_item_id`."""
    query = db.query(InventoryAdjustment).filter(
        InventoryAdjustment.restaurant_id == inventory.scope_for(db, user)
    )
    if inventory_item_id:
        _item_or_404(db, user, inventory_item_id)
        query = query.filter(InventoryAdjustment.inventory_item_id == inventory_item_id)
    return _adjustments_out(db, query.order_by(InventoryAdjustment.id.desc()).limit(limit).all())


@router.get("/suppliers", response_model=list[schemas.SupplierOut])
def list_suppliers(db: Session = Depends(get_db)):
    return crud.list_suppliers(db)


@router.post("/suppliers", response_model=schemas.SupplierOut)
def create_supplier(payload: schemas.SupplierCreate, db: Session = Depends(get_db)):
    supplier = Supplier(**payload.model_dump())
    if payload.lead_time_days is None:
        prefs = profile.inventory_settings(db)
        supplier.lead_time_days = prefs.default_lead_time_days if prefs else 3
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


@router.get("/staff", response_model=list[schemas.StaffOut])
def list_staff(db: Session = Depends(get_db)):
    return crud.list_staff(db)


@router.post("/staff", response_model=schemas.StaffOut)
def create_staff(payload: schemas.StaffCreate, db: Session = Depends(get_db)):
    if problem := profile.role_problem(db, payload.role):
        raise HTTPException(422, problem)
    staff = Staff(**payload.model_dump())
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


@router.get("/shifts", response_model=list[schemas.ShiftOut])
def list_shifts(db: Session = Depends(get_db)):
    return list(db.query(Shift).order_by(Shift.date, Shift.start_time))


@router.post("/shifts", response_model=schemas.ShiftOut)
def create_shift(payload: schemas.ShiftCreate, db: Session = Depends(get_db)):
    if not db.get(Staff, payload.staff_id):
        raise HTTPException(404, "Staff member not found")
    if payload.end_time <= payload.start_time:
        raise HTTPException(422, "A shift must end after it starts")
    if problems := profile.shift_problems(db, payload.staff_id, payload.date, payload.start_time, payload.end_time):
        raise HTTPException(422, " ".join(problems))
    shift = Shift(**payload.model_dump())
    db.add(shift)
    db.commit()
    db.refresh(shift)
    return shift


@router.get("/orders", response_model=list[schemas.OrderOut])
def list_orders(db: Session = Depends(get_db)):
    return list(db.query(Order).order_by(Order.created_at.desc()))


@router.post("/orders", response_model=schemas.OrderOut)
def create_order(payload: schemas.OrderCreate, db: Session = Depends(get_db)):
    """Record a sale and draw its ingredients out of stock.

    Dishes without a recipe consume nothing, so an order of those still
    goes through -- the link is opt-in per dish.
    """
    order = Order(channel=payload.channel, table_number=payload.table_number, status="completed")
    total = 0.0
    for line in payload.items:
        menu_item = db.get(MenuItem, line.menu_item_id)
        if not menu_item:
            raise HTTPException(404, f"Menu item {line.menu_item_id} not found")
        unit_price = menu_item.price
        total += unit_price * line.qty
        order.items.append(
            OrderItem(menu_item_id=menu_item.id, qty=line.qty, unit_price=unit_price, notes=line.notes)
        )
    order.total = total

    try:
        crud.check_and_deplete_stock(db, [(line.menu_item_id, line.qty) for line in payload.items])
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc

    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get("/menu-items/{menu_item_id}/recipe", response_model=schemas.RecipeOut)
def get_recipe(menu_item_id: str, db: Session = Depends(get_db)):
    item = db.get(MenuItem, menu_item_id)
    if not item:
        raise HTTPException(404, "Menu item not found")

    lines = []
    ingredient_cost = 0.0
    for component in crud.get_recipe(db, menu_item_id):
        ingredient = component.inventory_item
        line_cost = component.quantity * ingredient.unit_cost
        ingredient_cost += line_cost
        lines.append(
            {
                "inventory_item_id": ingredient.id,
                "name": ingredient.name,
                "unit": ingredient.unit,
                "quantity": component.quantity,
                "unit_cost": ingredient.unit_cost,
                "line_cost": round(line_cost, 2),
            }
        )
    lines.sort(key=lambda line: line["line_cost"], reverse=True)

    return {
        "menu_item_id": item.id,
        "menu_item_name": item.name,
        "price": item.price,
        "ingredient_cost": round(ingredient_cost, 2),
        "margin_pct": round(((item.price - ingredient_cost) / item.price) * 100, 1) if item.price else 0.0,
        "lines": lines,
    }


@router.put("/menu-items/{menu_item_id}/recipe", response_model=schemas.RecipeOut)
def set_recipe(
    menu_item_id: str, payload: schemas.RecipeIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Replace a dish's recipe wholesale.

    A replace rather than per-line edits: the UI edits the whole recipe
    at once, and this keeps it from drifting out of sync.
    """
    item = db.get(MenuItem, menu_item_id)
    if not item:
        raise HTTPException(404, "Menu item not found")

    # Only this restaurant's own stock can go into its dishes.
    restaurant_id = inventory.scope_for(db, user)
    seen = set()
    for line in payload.lines:
        if line.quantity <= 0:
            raise HTTPException(422, "Each ingredient quantity must be greater than zero")
        if not inventory.item_in(db, restaurant_id, line.inventory_item_id):
            raise HTTPException(404, f"Inventory item {line.inventory_item_id} not found")
        if line.inventory_item_id in seen:
            raise HTTPException(422, "The same ingredient is listed twice")
        seen.add(line.inventory_item_id)

    for existing in crud.get_recipe(db, menu_item_id):
        db.delete(existing)
    db.flush()
    for line in payload.lines:
        db.add(
            RecipeItem(
                menu_item_id=menu_item_id,
                inventory_item_id=line.inventory_item_id,
                quantity=line.quantity,
            )
        )
    db.commit()
    return get_recipe(menu_item_id, db)


@router.patch("/staff/{staff_id}", response_model=schemas.StaffOut)
def update_staff(staff_id: str, payload: schemas.StaffUpdate, db: Session = Depends(get_db)):
    staff = db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(404, "Staff member not found")
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("role") and fields["role"] != staff.role and (problem := profile.role_problem(db, fields["role"])):
        raise HTTPException(422, problem)
    for field, value in fields.items():
        setattr(staff, field, value)
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


@router.delete("/staff/{staff_id}", status_code=204)
def delete_staff(staff_id: str, db: Session = Depends(get_db)):
    staff = db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(404, "Staff member not found")

    # Shifts reference staff, and foreign keys are enforced -- refuse with an
    # explanation rather than letting the database raise an opaque error.
    shift_count = db.query(Shift).filter(Shift.staff_id == staff_id).count()
    if shift_count:
        raise HTTPException(
            409,
            f"{staff.name} still has {shift_count} scheduled "
            f"{'shift' if shift_count == 1 else 'shifts'}. Remove them first.",
        )

    db.delete(staff)
    db.commit()
    return None


@router.patch("/shifts/{shift_id}", response_model=schemas.ShiftOut)
def update_shift(shift_id: int, payload: schemas.ShiftUpdate, db: Session = Depends(get_db)):
    shift = db.get(Shift, shift_id)
    if not shift:
        raise HTTPException(404, "Shift not found")

    fields = payload.model_dump(exclude_unset=True)
    if "staff_id" in fields and not db.get(Staff, fields["staff_id"]):
        raise HTTPException(404, "Staff member not found")

    # Checked against the would-be shift before touching the row, so a
    # refusal leaves nothing half-changed in the session.
    after = {name: fields.get(name, getattr(shift, name)) for name in ("staff_id", "date", "start_time", "end_time")}
    if after["end_time"] <= after["start_time"]:
        raise HTTPException(422, "A shift must end after it starts")
    if problems := profile.shift_problems(db, exclude_shift_id=shift.id, **{
        "staff_id": after["staff_id"], "day": after["date"], "start": after["start_time"], "end": after["end_time"],
    }):
        raise HTTPException(422, " ".join(problems))
    for field, value in fields.items():
        setattr(shift, field, value)

    db.add(shift)
    db.commit()
    db.refresh(shift)
    return shift


@router.delete("/shifts/{shift_id}", status_code=204)
def delete_shift(shift_id: int, db: Session = Depends(get_db)):
    shift = db.get(Shift, shift_id)
    if not shift:
        raise HTTPException(404, "Shift not found")
    db.delete(shift)
    db.commit()
    return None


@router.get("/menu-performance", response_model=list[schemas.MenuPerformanceOut])
def menu_performance(db: Session = Depends(get_db)):
    """Every menu item with what it has actually sold and earned."""
    return crud.menu_performance(db)


@router.patch("/menu-items/{menu_item_id}", response_model=schemas.MenuItemOut)
def update_menu_item(menu_item_id: str, payload: schemas.MenuItemUpdate, db: Session = Depends(get_db)):
    item = db.get(MenuItem, menu_item_id)
    if not item:
        raise HTTPException(404, "Menu item not found")

    fields = payload.model_dump(exclude_unset=True)
    if fields.get("price") is not None and fields["price"] < 0:
        raise HTTPException(422, "Price must not be negative")
    if fields.get("cost") is not None and fields["cost"] < 0:
        raise HTTPException(422, "Cost must not be negative")

    for field, value in fields.items():
        setattr(item, field, value)
    if "price" in fields:
        # Editing the price by hand ends any promotion; otherwise its expiry
        # would later overwrite this price with the pre-promotion one.
        item.regular_price = None
        item.promo_ends_at = None
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/menu-items/{menu_item_id}", status_code=204)
def delete_menu_item(menu_item_id: str, db: Session = Depends(get_db)):
    """Hard-delete, allowed only for an item that has never sold.

    Once a dish appears on an order its row is part of the sales history
    the profit and marketing agents read, so taking it off the menu means
    setting `is_available` to false, not deleting it.
    """
    item = db.get(MenuItem, menu_item_id)
    if not item:
        raise HTTPException(404, "Menu item not found")

    sold = db.query(OrderItem).filter(OrderItem.menu_item_id == menu_item_id).count()
    if sold:
        raise HTTPException(
            409,
            f"'{item.name}' appears on {sold} past order "
            f"{'line' if sold == 1 else 'lines'}; deleting it would break sales history. "
            f"Mark it unavailable instead.",
        )

    db.delete(item)
    db.commit()
    return None


@router.get("/profit-summary", response_model=schemas.ProfitSummaryOut)
def profit_summary(
    days: int = Query(default=30, ge=1, le=365), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Revenue, COGS and gross profit over a trailing window, with a
    continuous daily series and category/channel breakdowns."""
    return crud.profit_summary(db, days=days, restaurant_id=inventory.scope_for(db, user))


@router.get("/supply-chain", response_model=schemas.SupplyChainOut)
def supply_chain(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Supplier exposure, concentration, and what needs reordering -- in this restaurant's stock."""
    return crud.supply_chain_summary(db, restaurant_id=inventory.scope_for(db, user))


@router.patch("/suppliers/{supplier_id}", response_model=schemas.SupplierOut)
def update_supplier(supplier_id: str, payload: schemas.SupplierUpdate, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    fields = payload.model_dump(exclude_unset=True)
    if fields.get("lead_time_days") is not None and fields["lead_time_days"] < 0:
        raise HTTPException(422, "Lead time cannot be negative")

    for field, value in fields.items():
        setattr(supplier, field, value)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


@router.delete("/suppliers/{supplier_id}", status_code=204)
def delete_supplier(supplier_id: str, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    supplied = db.query(InventoryItem).filter(InventoryItem.supplier_id == supplier_id).count()
    if supplied:
        raise HTTPException(
            409,
            f"{supplier.name} still supplies {supplied} "
            f"{'item' if supplied == 1 else 'items'}. Reassign them first.",
        )

    db.delete(supplier)
    db.commit()
    return None


@router.patch("/inventory-items/{inventory_item_id}", response_model=schemas.InventoryItemOut)
def update_inventory_item(
    inventory_item_id: str,
    payload: schemas.InventoryItemUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """An item's details -- name, unit, reorder point and quantity, cost, supplier. Owners and admins."""
    item = _item_or_404(db, user, inventory_item_id)
    if not user.is_manager:
        raise HTTPException(403, "Only the owner or an admin can change an item's details. You can record stock changes.")

    fields = payload.model_dump(exclude_unset=True)
    for required in ("name", "unit", "reorder_threshold", "reorder_qty", "unit_cost"):
        if required in fields and fields[required] is None:
            raise HTTPException(422, f"{required.replace('_', ' ').capitalize()} can't be empty.")
    if fields.get("name") and _name_taken(db, item.restaurant_id, fields["name"], except_id=item.id):
        raise HTTPException(409, f"Another item is already called {fields['name']}.")
    if fields.get("supplier_id") and not db.get(Supplier, fields["supplier_id"]):
        raise HTTPException(404, "Supplier not found")

    for field, value in fields.items():
        setattr(item, field, value)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/forecast", response_model=schemas.ForecastOut)
def forecast(
    days: int = Query(default=7, ge=1, le=28),
    history: int = Query(default=28, ge=7, le=180),
    db: Session = Depends(get_db),
):
    """Projected revenue and gross profit, built from completed trading days."""
    return crud.forecast(db, days_ahead=days, history_days=history)
