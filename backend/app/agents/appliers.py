"""Turning an approved proposal into a real change.

Until this module existed, approving an `AgentAction` only flipped a
status column -- the reorder never restocked anything and the shift
never reached the schedule. Each applier here performs the actual domain
mutation for one `action_type`.

Appliers never commit. The caller owns the transaction so that the
domain mutation, the action's status change, and the audit rows the
flush listener writes all land together or not at all.
"""

from datetime import date, datetime, time, timezone

from sqlalchemy.orm import Session

from app.agents.models import AgentAction
from app.restaurant import crud as restaurant_crud
from app.restaurant import orders
from app.restaurant.inventory import plural
from app.restaurant.models import MenuItem, Shift, Staff


class ApplyError(Exception):
    """An approved action could not be carried out.

    Raised rather than swallowed: an approval that silently does nothing
    is the bug this module exists to fix.
    """


def _require(payload: dict, key: str):
    value = payload.get(key)
    if value in (None, ""):
        raise ApplyError(f"Proposal payload is missing '{key}'")
    return value


def _positive_quantity(payload: dict) -> float:
    try:
        quantity = float(_require(payload, "quantity"))
    except (TypeError, ValueError) as exc:
        raise ApplyError(f"Quantity {payload.get('quantity')!r} is not a number") from exc
    if quantity <= 0:
        raise ApplyError(f"Quantity must be positive, got {quantity}")
    return quantity


def _place_order(db: Session, payload: dict, action: AgentAction | None) -> str:
    """Shared effect of `inventory_reorder` and `purchase_order`: the order is placed.

    Nothing reaches the stock figures yet. The order waits on the Inventory
    page under "On order" until someone receives it, or until its invoice
    arrives and receives it (app.prices.stock).
    """
    item_id = _require(payload, "inventory_item_id")
    quantity = _positive_quantity(payload)
    # The agent works for the site's restaurant; another's stock reads as missing.
    item = restaurant_crud.get_inventory_item(db, item_id)
    if item is None:
        raise ApplyError(f"No inventory item with id {item_id}")
    supplier = None
    supplier_id = payload.get("supplier_id") or item.supplier_id
    if supplier_id:
        supplier = restaurant_crud.get_supplier(db, supplier_id)
    packs = payload.get("packs")
    try:
        packs = float(packs) if packs is not None else None
    except (TypeError, ValueError):
        packs = None
    cost = payload.get("estimated_cost")
    try:
        cost = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost = None
    try:
        order = orders.place(
            db, item, quantity, supplier=supplier, packs=packs, pack_unit=payload.get("pack_unit") if packs else None,
            estimated_cost=cost, note=payload.get("note") or None, action_id=action.id if action else None,
            supplier_name=payload.get("supplier_name") or None, status=orders.DRAFT,
        )
    except orders.OrderError as exc:
        raise ApplyError(str(exc)) from exc
    amount = f"{quantity:g} {item.unit}"
    if packs and order.pack_unit:
        amount = f"{packs:g} {plural(order.pack_unit, packs)} ({amount})"
    where = f" from {order.supplier_name}" if order.supplier_name else ""
    due = f", expected {order.expected_on.strftime('%a %b %-d')}" if order.expected_on else ""
    return (
        f"Drafted an order for {amount} of {item.name}{where}{due}. Send it to the rep, or mark it placed, under On order on "
        f"the Inventory page; stock is booked when it is received, there or by its invoice."
    )


def _draft_lines(db: Session, payload: dict, action: AgentAction | None) -> str:
    """A drafted order of several lines to one supplier, from the weekly draft or the agent's tool."""
    from app.restaurant.inventory import site_scope

    supplier = restaurant_crud.get_supplier(db, _require(payload, "supplier_id"))
    if supplier is None:
        raise ApplyError("No such supplier")
    try:
        order = orders.draft_with_lines(db, site_scope(db), supplier, list(payload.get("lines") or []),
                                        note=(payload.get("note") or "")[:255] or None, action_id=action.id if action else None)
    except orders.OrderError as exc:
        raise ApplyError(str(exc)) from exc
    lines = len(order.lines)
    cost = f", about {order.estimated_cost:.2f}" if order.estimated_cost else ""
    return (f"Drafted an order of {lines} line{'s' if lines != 1 else ''} to {supplier.name}{cost}. Send it to the rep from On order "
            f"on the Inventory page, or from the order's own page. Nothing has been sent.")


def apply_inventory_reorder(db: Session, payload: dict, action: AgentAction | None = None) -> str:
    return _place_order(db, payload, action)


def apply_purchase_order(db: Session, payload: dict, action: AgentAction | None = None) -> str:
    if payload.get("lines"):
        return _draft_lines(db, payload, action)
    return _place_order(db, payload, action)


def apply_prep_sheet(db: Session, payload: dict, action: AgentAction | None = None) -> str:
    from app.forecast import prep
    from app.restaurant.inventory import site_scope

    sheet = prep.write_sheet(db, site_scope(db), payload, action.id if action else None)
    return f"Prep sheet for {sheet.date.isoformat()} confirmed: {len(sheet.lines)} ingredients. Print it from its page."


def apply_shift_change(db: Session, payload: dict) -> str:
    staff_id = _require(payload, "staff_id")
    from app.hr import staff as hr
    from app.restaurant.inventory import site_scope

    # The proposal's own restaurant (the approver's actor carries it): a
    # proposal naming another restaurant's employee finds no one.
    staff = hr.person_in(db, site_scope(db), staff_id)
    if staff is None:
        raise ApplyError(f"No staff member with id {staff_id}")

    try:
        shift_date = date.fromisoformat(_require(payload, "date"))
        start_time = time.fromisoformat(_require(payload, "start_time"))
        end_time = time.fromisoformat(_require(payload, "end_time"))
    except ValueError as exc:
        raise ApplyError(f"Shift date/time is not valid ISO format: {exc}") from exc

    if end_time <= start_time:
        raise ApplyError(f"Shift ends ({end_time}) at or before it starts ({start_time})")

    from app.restaurant import profile

    role = payload.get("role") or staff.role
    if problem := profile.role_problem(db, role):
        raise ApplyError(problem)
    if problems := profile.shift_problems(db, staff_id, shift_date, start_time, end_time):
        raise ApplyError(f"This shift breaks the restaurant's scheduling rules: {' '.join(problems)}")

    # A draft, like any shift a manager adds: approving the proposal puts it
    # on the schedule, and publishing shows it to the staff.
    shift = Shift(
        restaurant_id=staff.restaurant_id,
        staff_id=staff_id,
        date=shift_date,
        start_time=start_time,
        end_time=end_time,
        role=role,
    )
    db.add(shift)
    return f"Shift added to the draft schedule: {staff.name} on {shift_date} {start_time}-{end_time} as {shift.role}"


def _promo_end(raw) -> datetime | None:
    """Parse an optional promotion end time. Naive values are taken as UTC."""
    if raw in (None, ""):
        return None
    try:
        ends_at = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise ApplyError(f"promo_ends_at {raw!r} is not an ISO date or datetime") from exc
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    if ends_at <= datetime.now(timezone.utc):
        raise ApplyError("A promotion cannot end in the past")
    return ends_at


def apply_menu_change(db: Session, payload: dict) -> str:
    item_id = _require(payload, "menu_item_id")
    item = restaurant_crud.get_menu_item(db, item_id)
    if item is None:
        raise ApplyError(f"No menu item with id {item_id}")

    change_type = _require(payload, "change_type")

    # A promotion is a temporary price cut. With no promotions table the
    # effect is the same as a price change; the change log keeps the original
    # price, so ending the promo is a revert rather than a second guess.
    if change_type in ("price_change", "promotion"):
        raw = payload.get("new_price")
        if raw is None:
            raise ApplyError(
                f"This {change_type.replace('_', ' ')} needs a price. The proposal only "
                f"describes it in words ({payload.get('details') or 'no detail'!r}), which "
                f"cannot be applied on its own -- approve it with a price, or reject it."
            )
        try:
            new_price = float(raw)
        except (TypeError, ValueError) as exc:
            raise ApplyError(f"new_price {raw!r} is not a number") from exc
        if new_price < 0:
            raise ApplyError(f"new_price must not be negative, got {new_price}")
        previous = item.price

        if change_type == "promotion":
            ends_at = _promo_end(payload.get("promo_ends_at"))
            # Stacking promotions must not lose the real price: keep the first one.
            if item.regular_price is None:
                item.regular_price = previous
            item.promo_ends_at = ends_at
            item.price = new_price
            db.add(item)
            until = f" until {ends_at:%Y-%m-%d %H:%M} UTC" if ends_at else " (no end date)"
            return f"Promotion applied: {item.name} {previous:.2f} -> {new_price:.2f}{until}"

        # A deliberate price change supersedes any running promotion; letting
        # the promo expire later would overwrite the new price with the old one.
        item.regular_price = None
        item.promo_ends_at = None
        item.price = new_price
        db.add(item)
        return f"Price changed: {item.name} {previous:.2f} -> {new_price:.2f}"

    if change_type == "remove":
        if not item.is_available:
            raise ApplyError(f"'{item.name}' is already off the menu")
        item.is_available = False
        db.add(item)
        # Soft-remove: order_items still reference this row, and hard-deleting
        # it would break the sales history the profit agent reads.
        return f"Removed from menu: {item.name} (marked unavailable)"

    if change_type == "description_update":
        details = _require(payload, "details")
        previous = item.description
        item.description = details
        db.add(item)
        return f"Description updated for {item.name} (was {previous!r})"

    raise ApplyError(f"Unknown menu change_type {change_type!r}")


#: action_type -> applier. A proposal whose type is absent here cannot be
#: approved, which is intentional: better a loud failure than an approval
#: that quietly changes nothing.
def apply_recipe(db: Session, payload: dict) -> str:
    """A confirmed recipe from the proposal's lines (the operator may have edited quantities or dropped lines)."""
    from app.audit.context import get_actor
    from app.recipes import engine as recipes
    from app.restaurant.inventory import site_scope

    restaurant_id = site_scope(db)
    recipe = recipes.write_recipe(db, restaurant_id, payload, confirmed_by=get_actor().id)
    priced = len([line for line in recipe.lines])
    return f"Recipe confirmed: {recipe.name}, {priced} line{'s' if priced != 1 else ''}. Plate cost is computed with the next weekly run, or now from its page."


def apply_agreed_price(db: Session, payload: dict) -> str:
    """An agreed price on the books: from the proposal's effective date, superseding any earlier one for the pair."""
    from datetime import date as _date

    from app.audit.context import get_actor
    from app.prices.models import AgreedPrice, PriceItem
    from app.restaurant.inventory import site_scope

    restaurant_id = site_scope(db)
    item = db.get(PriceItem, _require(payload, "item_id"))
    if item is None or item.restaurant_id != restaurant_id:
        raise ApplyError("No such item for this restaurant")
    supplier = restaurant_crud.get_supplier(db, _require(payload, "supplier_id"))
    if supplier is None:
        raise ApplyError("No such supplier")
    try:
        unit_price = float(_require(payload, "unit_price"))
        effective = _date.fromisoformat(str(payload.get("effective_from") or _date.today().isoformat())[:10])
    except (TypeError, ValueError) as exc:
        raise ApplyError(f"Bad price or date in the proposal: {exc}") from exc
    if unit_price <= 0:
        raise ApplyError("An agreed price must be positive")
    for earlier in db.query(AgreedPrice).filter(AgreedPrice.restaurant_id == restaurant_id, AgreedPrice.item_id == item.id,
                                                AgreedPrice.supplier_id == supplier.id, AgreedPrice.effective_to.is_(None)):
        if earlier.effective_from < effective:
            earlier.effective_to = effective
    db.add(AgreedPrice(restaurant_id=restaurant_id, supplier_id=supplier.id, item_id=item.id, unit_price=unit_price, base_unit=item.unit,
                       effective_from=effective, source_document_id=payload.get("source_document_id"), confirmed_by=get_actor().id))
    db.flush()
    return f"Agreed price on the books: {item.name} from {supplier.name} at {unit_price:.4g} per {item.unit} from {effective.isoformat()}. The next digest checks every invoice against it."


def apply_menu_reprice(db: Session, payload: dict) -> str:
    """Nothing changes here: the owner changes the price in the POS. Approval records that they mean to."""
    return (f"Noted: {payload.get('item_name', 'the dish')} to be repriced by about {float(payload.get('suggested_delta') or 0):.2f}. "
            "Change it in your POS; FullHouse never writes to a menu.")


APPLIERS = {
    "inventory_reorder": apply_inventory_reorder,
    "purchase_order": apply_purchase_order,
    "shift_change": apply_shift_change,
    "menu_change": apply_menu_change,
    "recipe": apply_recipe,
    "agreed_price": apply_agreed_price,
    "menu_reprice": apply_menu_reprice,
    "prep_sheet": apply_prep_sheet,
}


def apply_action(db: Session, action: AgentAction) -> str:
    """Carry out `action`'s effect and return a description of what changed.

    Does not commit -- see the module docstring.
    """
    applier = APPLIERS.get(action.action_type)
    if applier is None:
        raise ApplyError(
            f"No applier for action_type {action.action_type!r}; "
            f"known types: {', '.join(sorted(APPLIERS))}"
        )
    if action.action_type in ("inventory_reorder", "purchase_order", "prep_sheet"):
        return applier(db, action.payload or {}, action)
    return applier(db, action.payload or {})


#: Extra fields the operator must supply for an action whose proposal left
#: them unset. Surfaced on the action so the queue can ask up front rather
#: than failing after the Approve button is pressed.
def missing_inputs(action: AgentAction) -> list[str]:
    payload = action.payload or {}
    if action.action_type == "menu_change":
        if payload.get("change_type") in ("price_change", "promotion") and payload.get("new_price") is None:
            return ["new_price"]
    return []


def is_appliable(action: AgentAction) -> bool:
    """Whether any applier knows how to carry this action out at all."""
    return action.action_type in APPLIERS
