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
from app.restaurant.models import InventoryItem, MenuItem, Shift, Staff


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


def _restock(db: Session, payload: dict, source: str) -> str:
    """Shared effect of `inventory_reorder` and `purchase_order`.

    Simplification worth knowing about: there is no purchase-order
    lifecycle in the schema, so approval books the goods as received
    immediately instead of ordering now and receiving on delivery.
    """
    item_id = _require(payload, "inventory_item_id")
    quantity = _positive_quantity(payload)

    item = db.get(InventoryItem, item_id)
    if item is None:
        raise ApplyError(f"No inventory item with id {item_id}")

    previous = item.quantity_on_hand
    item.quantity_on_hand = previous + quantity
    db.add(item)
    return (
        f"{source}: {item.name} stock {previous:g} -> {item.quantity_on_hand:g} {item.unit} "
        f"(+{quantity:g})"
    )


def apply_inventory_reorder(db: Session, payload: dict) -> str:
    return _restock(db, payload, "Reorder received")


def apply_purchase_order(db: Session, payload: dict) -> str:
    supplier = payload.get("supplier_name") or payload.get("supplier_id") or "unknown supplier"
    return _restock(db, payload, f"Purchase order from {supplier} received")


def apply_shift_change(db: Session, payload: dict) -> str:
    staff_id = _require(payload, "staff_id")
    staff = db.get(Staff, staff_id)
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

    shift = Shift(
        staff_id=staff_id,
        date=shift_date,
        start_time=start_time,
        end_time=end_time,
        role=payload.get("role") or staff.role,
    )
    db.add(shift)
    return f"Shift scheduled: {staff.name} on {shift_date} {start_time}-{end_time} as {shift.role}"


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
    item = db.get(MenuItem, item_id)
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
APPLIERS = {
    "inventory_reorder": apply_inventory_reorder,
    "purchase_order": apply_purchase_order,
    "shift_change": apply_shift_change,
    "menu_change": apply_menu_change,
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
