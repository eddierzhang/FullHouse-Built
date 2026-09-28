"""What a change was to, in words: "Tomatoes", "Friday's shift for Sam Patel".

The change log stores table names and ids -- right for undoing, useless to
a person scanning the History page. A label comes from the change's own
snapshot where it has a name (so a deleted row still reads right), and
otherwise from the row as it is now.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.audit.models import ChangeLog

#: Tables whose rows have a `name` column worth showing.
_NAMED = ("inventory_items", "menu_items", "suppliers", "staff")


def _snapshot_name(change: ChangeLog) -> str | None:
    for snapshot in (change.after, change.before):
        if snapshot and snapshot.get("name"):
            return str(snapshot["name"])
    return None


def _proposal_title(payload: dict | None) -> str:
    payload = payload or {}
    for key in ("item_name", "staff_name", "supplier_name"):
        if payload.get(key):
            return f"proposal for {payload[key]}"
    return "a proposal"


def labels_for(db: Session, changes: list[ChangeLog]) -> dict[int, str]:
    """change id -> a short name for what it changed."""
    from app.agents.models import AgentAction
    from app.restaurant.models import InventoryItem, MenuItem, Restaurant, Shift, Staff, Supplier

    models = {"inventory_items": InventoryItem, "menu_items": MenuItem, "suppliers": Supplier, "staff": Staff}
    wanted: dict[str, set[str]] = defaultdict(set)
    for change in changes:
        wanted[change.entity_type].add(change.entity_id)

    names: dict[tuple[str, str], str] = {}
    for table, model in models.items():
        if wanted.get(table):
            for row_id, name in db.query(model.id, model.name).filter(model.id.in_(wanted[table])):
                names[(table, str(row_id))] = name
    if wanted.get("agent_actions"):
        for action in db.query(AgentAction).filter(AgentAction.id.in_(wanted["agent_actions"])):
            names[("agent_actions", action.id)] = _proposal_title(action.payload)
    if wanted.get("restaurants"):
        for row_id, name in db.query(Restaurant.id, Restaurant.name).filter(Restaurant.id.in_(wanted["restaurants"])):
            names[("restaurants", row_id)] = name or "the restaurant"

    # An edit's snapshot holds only what changed; a shift's person and date come from the row.
    shifts = {}
    if wanted.get("shifts"):
        ids = [int(i) for i in wanted["shifts"] if str(i).isdigit()]
        shifts = {str(s.id): {"staff_id": s.staff_id, "date": s.date.isoformat()} for s in db.query(Shift).filter(Shift.id.in_(ids))}

    # Shifts and recipe lines name their person or dish only by id.
    staff_ids = {
        ({**shifts.get(c.entity_id, {}), **(c.before or {}), **(c.after or {})}).get("staff_id")
        for c in changes if c.entity_type == "shifts"
    } - {None}
    dish_ids = {(c.after or c.before or {}).get("menu_item_id") for c in changes if c.entity_type == "recipe_items"} - {None}
    staff_names = dict(db.query(Staff.id, Staff.name).filter(Staff.id.in_(staff_ids))) if staff_ids else {}
    dish_names = dict(db.query(MenuItem.id, MenuItem.name).filter(MenuItem.id.in_(dish_ids))) if dish_ids else {}

    labels = {}
    for change in changes:
        snapshot = change.after or change.before or {}
        table = change.entity_type
        if table in _NAMED or table == "restaurants":
            label = _snapshot_name(change) or names.get((table, change.entity_id))
        elif table == "agent_actions":
            label = names.get((table, change.entity_id)) or _proposal_title(snapshot.get("payload"))
        elif table == "shifts":
            shift = {**shifts.get(change.entity_id, {}), **(change.before or {}), **(change.after or {})}
            who = staff_names.get(shift.get("staff_id"), "someone")
            label = f"{who}'s shift on {shift.get('date', '?')}"
        elif table == "recipe_items":
            dish = dish_names.get(snapshot.get("menu_item_id"))
            label = f"recipe for {dish}" if dish else "a recipe"
        elif table == "orders":
            label = "a sale"
        elif table == "agent_runs":
            label = "an agent run"
        else:
            label = None
        labels[change.id] = label or table.replace("_", " ")
    return labels
