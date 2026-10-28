"""Three plain sentences under every proposal: the issue, what approving changes, and what the owner should know.

The agent's own note says why it proposed something; the applier says what approving does; neither says both,
and neither says what to watch for. This module writes all three from the payload, in the same words for every
proposal of a kind, so the Recommendations inbox reads the same whether a model or the rule-based assistant wrote
the proposal. Where a lookup makes a sentence truer (stock on hand, a menu price), it is made here, read-only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.agents.models import AgentAction
from app.restaurant import crud as restaurant_crud
from app.restaurant.inventory import plural


@dataclass
class Explanation:
    #: What the agent saw: the shortfall, the document, the forecast.
    issue: str
    #: What pressing Approve changes in FullHouse, and what it does not.
    change: str
    #: What the owner should know before deciding: the limits, the source, the next step that is theirs.
    know: str

    def as_dict(self) -> dict:
        return asdict(self)


def _money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "an unknown amount"


def _num(value) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value or "")


def _day(value) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%b %-d")
    except (TypeError, ValueError):
        return str(value or "a date")


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _inventory(db: Session | None, payload: dict):
    item_id = payload.get("inventory_item_id")
    if db is None or not item_id:
        return None
    try:
        return restaurant_crud.get_inventory_item(db, item_id)
    except Exception:  # noqa: BLE001 - an explanation never breaks a listing
        return None


def _order(db: Session | None, payload: dict) -> Explanation:
    item = _text(payload.get("item_name")) or "an item"
    supplier = _text(payload.get("supplier_name"))
    where = f" from {supplier}" if supplier else ""
    stock = _inventory(db, payload)
    note = _text(payload.get("note"))
    if stock is not None and stock.quantity_on_hand is not None and stock.reorder_threshold is not None:
        issue = (f"{item} is at {stock.quantity_on_hand:g} {stock.unit} on hand against a reorder point of "
                 f"{stock.reorder_threshold:g} {stock.unit}.")
        if note and "on hand" not in note:
            issue += f" {note}"
    else:
        issue = note or f"{item} is at or below its reorder point."
    packs = payload.get("packs")
    amount = f"{_num(payload.get('quantity'))} {stock.unit if stock is not None else ''}".strip()
    if packs and payload.get("pack_unit"):
        try:
            amount = f"{float(packs):g} {plural(str(payload['pack_unit']), float(packs))} ({amount})"
        except (TypeError, ValueError):
            pass
    cost = f", about {_money(payload['estimated_cost'])}" if payload.get("estimated_cost") else ""
    change = (f"Approving drafts an order for {amount} of {item}{where}{cost}. Nothing is sent: you send it to the rep, or "
              f"mark it placed by phone, under On order on the Inventory page. Stock is booked when the delivery is "
              f"received there or when its invoice arrives.")
    priced = (f"The price is {supplier}'s from {_day(payload['priced_on'])}; the rep may quote differently. "
              if payload.get("priced_on") and supplier else "The price is from the last invoice, so the rep may quote differently. ")
    know = priced + "If you already ordered this by phone, press Not now and record the order under On order instead."
    return Explanation(issue, change, know)


def _order_lines(payload: dict) -> Explanation:
    supplier = _text(payload.get("supplier_name")) or "the supplier"
    lines = list(payload.get("lines") or [])
    count = len(lines)
    note = _text(payload.get("note"))
    issue = (f"Your usual weekly order from {supplier} is due: {count} item{'s' if count != 1 else ''} you buy from them "
             f"most weeks, with quantities at your weekly median.")
    if note.startswith("Your usual week"):
        issue = note.split(". About")[0] + "."
    total = f", about {_money(payload['expected_total'])}" if payload.get("expected_total") else ""
    change = (f"Approving drafts one order of {count} line{'s' if count != 1 else ''} to {supplier}{total}. Nothing is sent until "
              f"you send it from On order on the Inventory page or from the order's own page, where you can still change it.")
    know = ("Quantities are what you usually buy, not what is on the shelf; drop a line you don't need before approving. "
            "Expected prices are from the last invoices, and a short delivery against this order shows in the digest.")
    return Explanation(issue, change, know)


def _shift(payload: dict) -> Explanation:
    staff = _text(payload.get("staff_name")) or "a staff member"
    day = _day(payload.get("date"))
    role = _text(payload.get("role"))
    issue = _text(payload.get("note")) or f"A shift on {day} needs covering."
    change = (f"Approving adds a draft shift for {staff} on {day}, {_text(payload.get('start_time'))} to "
              f"{_text(payload.get('end_time'))}{' as ' + role if role else ''}. Staff don't see it until you publish the "
              f"schedule from Schedule & staff.")
    know = ("It is checked against your scheduling rules at approval and refused if it breaks one (rest between shifts, "
            "weekly hours). Approving sends nothing to the employee.")
    return Explanation(issue, change, know)


def _menu(db: Session | None, payload: dict) -> Explanation:
    item = _text(payload.get("item_name")) or "the dish"
    kind = _text(payload.get("change_type")) or "change"
    details = _text(payload.get("details"))
    issue = details or f"The agent suggests a {kind.replace('_', ' ')} for {item}."
    current = None
    if db is not None and payload.get("menu_item_id"):
        try:
            row = restaurant_crud.get_menu_item(db, payload["menu_item_id"])
            current = row.price if row is not None else None
        except Exception:  # noqa: BLE001
            current = None
    now = f" from {_money(current)}" if current is not None else ""
    price = payload.get("new_price")
    if kind == "promotion":
        ends = f" until {_day(payload['promo_ends_at'])}" if payload.get("promo_ends_at") else ", with no end date"
        change = (f"Approving sets {item}{now} to {_money(price) if price is not None else 'the price you enter'}{ends}. "
                  f"The regular price is kept and comes back when the promotion ends.")
        know = ("This changes FullHouse's menu record only; set the same price in your POS. Undo from History & undo "
                "puts the regular price back early.")
    elif kind == "price_change":
        change = f"Approving sets {item}{now} to {_money(price) if price is not None else 'the price you enter'} on FullHouse's menu record."
        know = ("The POS is not changed; set the price there too. Any running promotion on the dish ends. Undo from "
                "History & undo.")
    elif kind == "remove":
        change = f"Approving marks {item} unavailable on FullHouse's menu. Its sales history is kept."
        know = "Take it off the POS yourself. Undo from History & undo puts it back."
    elif kind == "description_update":
        change = f"Approving replaces the description of {item} with the text above."
        know = "Menus printed or shown elsewhere don't change on their own. Undo from History & undo."
    else:
        change = f"Approving applies this {kind.replace('_', ' ')} to {item}."
        know = "Undo from History & undo."
    return Explanation(issue, change, know)


def _recipe(payload: dict) -> Explanation:
    name = _text(payload.get("name")) or "this dish"
    lines = list(payload.get("lines") or [])
    unknown = [u for u in (payload.get("unknown") or []) if u]
    issue = (f"{name} has no confirmed recipe, so its plate cost and margin can't be worked out from your invoices.")
    change = (f"Approving saves the recipe with these {len(lines)} line{'s' if len(lines) != 1 else ''}. Plate cost is then "
              f"computed from your invoice prices, and the Menu margin section of the digest watches it. Nothing changes on "
              f"the menu or in the POS.")
    if unknown:
        know = (f"{len(unknown)} line{'s' if len(unknown) != 1 else ''} ({', '.join(unknown[:4])}{'…' if len(unknown) > 4 else ''}) "
                f"{'are' if len(unknown) != 1 else 'is'} not on your invoices and {'are' if len(unknown) != 1 else 'is'} left out "
                f"of the cost until you add {'them' if len(unknown) != 1 else 'it'}. Change any quantity before approving.")
    else:
        know = "Every line matched an invoice item. Change any quantity before approving; you can edit the recipe later from its page."
    return Explanation(issue, change, know)


def _agreed(payload: dict) -> Explanation:
    item = _text(payload.get("item_name")) or "this item"
    supplier = _text(payload.get("supplier_name")) or "the supplier"
    per_pack = payload.get("per_pack") or payload.get("unit_price")
    pack = _text(payload.get("pack_sentence"))
    since = _day(payload.get("effective_from")) if payload.get("effective_from") else "today"
    source = "a price list you uploaded" if payload.get("source_document_id") else "an invoice line"
    issue = (f"{supplier} lists {item} at {_money(per_pack)} a pack{' (' + pack + ')' if pack else ''} from {since}, read from "
             f"{source}.")
    change = (f"Approving puts that agreed price on the books from {since}. Every later invoice line for {item} from {supplier} "
              f"is checked against it, and anything charged above it appears in the digest under Owed to you, with a credit "
              f"claim drafted.")
    know = ("It replaces any earlier agreed price for this item and supplier. Nothing is sent to the supplier. Reject if the "
            "list price has a condition the reader could not see, like a minimum order.")
    return Explanation(issue, change, know)


def _reprice(payload: dict) -> Explanation:
    item = _text(payload.get("item_name")) or "the dish"
    before, after, price = payload.get("plate_cost_before"), payload.get("plate_cost_after"), payload.get("current_price")
    delta = payload.get("suggested_delta")
    moved = (f"The plate cost of {item} went from {_money(before)} to {_money(after)}" if before is not None
             else f"The plate cost of {item} is {_money(after)}")
    issue = moved + (f", and at {_money(price)} a plate the margin has slipped." if price else ".")
    change = (f"Approving records that you mean to raise {item} by about {_money(delta)}. FullHouse never writes to a menu "
              f"or a POS, so nothing else changes.")
    know = ("Change the price in your POS yourself. The suggestion keeps a 70% margin on the plate cost from your latest "
            "invoices; round it to a price that suits the menu.")
    return Explanation(issue, change, know)


def _calendar(payload: dict) -> Explanation:
    title = _text(payload.get("title")) or "this date"
    kind = _text(payload.get("kind")).replace("_", " ") or "date"
    due = _day(payload.get("due_on"))
    issue = _text(payload.get("note")) or f"{title} comes due {due}."
    repeat = _text(payload.get("recurrence"))
    change = f"Approving puts {title} on the calendar as a {kind} due {due}" + (f", repeating {repeat}" if repeat else "") + "."
    if payload.get("asset"):
        asset = payload["asset"] or {}
        change += f" {asset.get('name') or 'The equipment'} is added to the equipment register with its warranty date."
    know = ("It shows under Coming due in the digest as the date nears, and overdue after it. Attaching the renewed document "
            "to the item marks it done and makes the next one. Reject if the date was read wrong; you can also type dates in "
            "on the Calendar page.")
    return Explanation(issue, change, know)


def _prep(payload: dict) -> Explanation:
    lines = list(payload.get("lines") or [])
    day = _day(payload.get("date"))
    issue = (f"The sales forecast for {day} and your confirmed recipes call for {len(lines)} ingredient"
             f"{'s' if len(lines) != 1 else ''} to be prepped.")
    change = f"Approving confirms the prep sheet for {day}; print it from its page. Nothing is ordered and stock is not changed."
    know = ("Quantities are forecast sales times recipe lines, so a dish without a recipe is missing from it. Last week's "
            "forecast accuracy is on the digest; trim or add before approving if you know something the forecast does not.")
    return Explanation(issue, change, know)


def explain(action: AgentAction, db: Session | None = None) -> Explanation:
    """The three sentences for a proposal of any kind. Unknown kinds get the agent's note and an honest shrug."""
    payload = action.payload or {}
    kind = action.action_type
    if kind in ("inventory_reorder", "purchase_order"):
        return _order_lines(payload) if payload.get("lines") else _order(db, payload)
    if kind == "shift_change":
        return _shift(payload)
    if kind == "menu_change":
        return _menu(db, payload)
    if kind == "recipe":
        return _recipe(payload)
    if kind == "agreed_price":
        return _agreed(payload)
    if kind == "menu_reprice":
        return _reprice(payload)
    if kind == "calendar_item":
        return _calendar(payload)
    if kind == "prep_sheet":
        return _prep(payload)
    note = _text(payload.get("note")) or _text(payload.get("details")) or "The agent left no note."
    return Explanation(note, f"No part of FullHouse can carry out a {kind.replace('_', ' ')} proposal, so approving it changes nothing.",
                       "Reject it; the run behind it is on the Agent page.")
