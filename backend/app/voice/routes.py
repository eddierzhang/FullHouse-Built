"""Voice commands: a transcript becomes a preview, and only a confirm writes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.platform import flags
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


def _scope(db: Session, user: User) -> str:
    """The signed-in user's restaurant, with this feature switched on for it."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.enabled(db, restaurant_id, flags.V3_VOICE):
        raise HTTPException(404, "Voice isn't switched on for this restaurant (Profile, Features).")
    return restaurant_id


from datetime import datetime, timezone  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from app.notify import service as notify  # noqa: E402
from app.restaurant import inventory  # noqa: E402
from app.restaurant.models import DRAFT, OPEN_ORDER_STATUSES, InventoryItem, PurchaseOrder, Supplier  # noqa: E402
from app.voice import parser  # noqa: E402
from app.voice.models import SpokenAlias, VoiceCommand  # noqa: E402

ROUTE_LABELS = {"inventory": "Inventory", "receive": "Deliveries", "count": "the count sheet", "variance": "Variance & waste", "digest": "the digest",
                "insights": "Savings & costs", "autopilot": "Ordering autopilot", "approvals": "Recommendations", "staff": "Schedule & staff",
                "menu": "the menu", "invoices": "Invoices", "suppliers": "Suppliers", "profit": "Profit & forecast", "overview": "Today"}


class ParseIn(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)
    #: Where the words came from: the browser's own recognition, or typed.
    source: str = Field(default="browser", pattern="^(browser|typed|server)$")


class LineIn(BaseModel):
    inventory_item_id: str
    quantity: float = Field(ge=0, le=1_000_000)


class ConfirmIn(BaseModel):
    #: The lines as the person left them on the preview: amounts corrected, unclear items picked. Null keeps the preview's.
    lines: list[LineIn] | None = Field(default=None, max_length=200)
    reason: str | None = None


class AliasIn(BaseModel):
    phrase: str = Field(min_length=1, max_length=120)
    inventory_item_id: str


class TranscribeIn(BaseModel):
    #: Base64 audio, for a browser that can record but not recognise.
    audio: str = Field(min_length=1, max_length=8_000_000)
    seconds: float = Field(gt=0, le=120)


def _vocabulary(db: Session, restaurant_id: str) -> tuple[list[dict], dict[str, str], list[str], list[str]]:
    from app.stock.models import StorageArea

    items = [{"id": i.id, "name": i.name, "unit": i.unit, "pack_size": i.pack_size, "pack_unit": i.pack_unit}
             for i in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id)]
    aliases = {a.phrase: a.inventory_item_id for a in db.query(SpokenAlias).filter(SpokenAlias.restaurant_id == restaurant_id)}
    areas = [a.name for a in db.query(StorageArea).filter(StorageArea.restaurant_id == restaurant_id)]
    suppliers = [s.name for s in db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id)]
    return items, aliases, areas, suppliers


def _needs(db: Session, restaurant_id: str, intent: str) -> str | None:
    """Why this kind of command can't be carried out here, if it can't."""
    if intent in ("count", "waste") and not flags.enabled(db, restaurant_id, flags.V3_STOCK):
        return "Stock tracking isn't switched on, so counts and waste can't be saved by voice."
    if intent == "receive" and not flags.enabled(db, restaurant_id, flags.V3_RECEIVING):
        return "Deliveries aren't switched on, so a delivery can't be checked in by voice."
    return None


def _open_order(db: Session, restaurant_id: str, supplier_name: str | None) -> PurchaseOrder | None:
    """The order a spoken delivery is for: the supplier's oldest placed order not yet in."""
    query = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.status.in_(OPEN_ORDER_STATUSES),
                                           PurchaseOrder.status != DRAFT)
    rows = [o for o in query.order_by(PurchaseOrder.ordered_at) if not supplier_name or o.supplier_name == supplier_name]
    return rows[0] if rows and (supplier_name or len(rows) == 1) else None


def _out(db: Session, command: VoiceCommand) -> dict:
    parsed = command.parsed or {}
    return {"id": command.id, "intent": command.intent, "status": command.status, "transcript": command.transcript, **parsed}


@router.post("/parse")
def parse(body: ParseIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Turn what was said into a preview. Nothing is saved to stock, orders or anything else: only the preview itself."""
    restaurant_id = _scope(db, user)
    items, aliases, areas, suppliers = _vocabulary(db, restaurant_id)
    command = parser.parse(body.transcript, items, aliases, areas, suppliers)
    parsed = command.as_dict()
    parsed["speak"] = parser.read_back(command)
    parsed["blocked"] = _needs(db, restaurant_id, command.intent)
    parsed["route_label"] = ROUTE_LABELS.get(command.route or "")
    if command.intent == "receive":
        order = _open_order(db, restaurant_id, command.supplier)
        parsed["order_id"] = order.id if order else None
        if order is None:
            parsed["blocked"] = parsed["blocked"] or (f"There is no order from {command.supplier} on its way." if command.supplier
                                                      else "Say which supplier the delivery is from.")
        elif not command.all_arrived:
            parsed["blocked"] = "Say \\u201ceverything arrived\\u201d or \\u201ceverything arrived except\\u2026\\u201d, or check it in on the Deliveries page."
    row = VoiceCommand(restaurant_id=restaurant_id, user_email=user.email, transcript=body.transcript[:2000], intent=command.intent,
                       parsed=parsed, source=body.source)
    db.add(row)
    db.commit()
    return _out(db, row)


@router.post("/commands/{command_id}/confirm")
def confirm(command_id: str, body: ConfirmIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Carry out a previewed command. This, and only this, writes. Confirming twice does it once."""
    restaurant_id = _scope(db, user)
    command = db.query(VoiceCommand).filter(VoiceCommand.restaurant_id == restaurant_id, VoiceCommand.id == command_id).first()
    if command is None:
        raise HTTPException(404, "Nothing previewed under that id")
    if command.status == "confirmed":
        return {**_out(db, command), "done": (command.parsed or {}).get("done")}
    if command.status != "previewed":
        raise HTTPException(409, "That was discarded. Say it again.")
    parsed = dict(command.parsed or {})
    blocked = _needs(db, restaurant_id, command.intent) or parsed.get("blocked")
    if blocked:
        raise HTTPException(409, blocked)
    lines = [l.model_dump() for l in body.lines] if body and body.lines is not None else [
        {"inventory_item_id": l["inventory_item_id"], "quantity": l["quantity"]} for l in parsed.get("lines", [])]
    owned = {i.id: i for i in db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id,
                                                           InventoryItem.id.in_([l["inventory_item_id"] for l in lines] or [""]))}
    lines = [l for l in lines if l["inventory_item_id"] in owned]
    done: str
    if command.intent == "count":
        if not lines:
            raise HTTPException(422, "There is nothing to count in that.")
        from app.stock import engine as stock

        inventory.lock_items(db, [l["inventory_item_id"] for l in lines])
        _, summary = stock.count_now(db, restaurant_id, [{"inventory_item_id": l["inventory_item_id"], "counted_qty": l["quantity"]} for l in lines],
                                     started_by=user.email, client_key=f"voice:{command.id}", area_name=parsed.get("area"))
        stock.rollup(db, restaurant_id, days=3)
        done = f"Counted {summary['counted']} item{'s' if summary['counted'] != 1 else ''}."
    elif command.intent == "waste":
        if not lines:
            raise HTTPException(422, "There is nothing to log in that.")
        from app.stock import engine as stock

        reason = (body.reason if body and body.reason else parsed.get("reason")) or "other"
        if reason not in stock.WASTE_REASONS:
            raise HTTPException(422, "Pick a reason for the waste.")
        inventory.lock_items(db, [l["inventory_item_id"] for l in lines])
        taken = 0.0
        for line in lines:
            item = owned[line["inventory_item_id"]]
            db.refresh(item)
            taken += stock.record_waste(db, item, line["quantity"], reason, "by voice", source_key=f"voice:{command.id}:{item.id}") * item.unit_cost
        stock.rollup(db, restaurant_id, days=3)
        done = f"Logged {len(lines)} waste entr{'ies' if len(lines) != 1 else 'y'}, about ${taken:,.2f}."
    elif command.intent == "receive":
        from app.receiving import engine as receiving

        order = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.id == parsed.get("order_id")).first()
        if order is None:
            raise HTTPException(409, "That order is no longer on its way.")
        short = {l["inventory_item_id"]: l["quantity"] for l in lines}  # in stock units: how much did not arrive
        entries = []
        for view in receiving._line_views(order):
            missing = short.get(view["inventory_item_id"] or "", 0.0)
            line = next((pl for pl in order.lines if pl.id == view["key"]), None)
            per_pack = (line.units / line.qty) if line is not None and line.units and line.qty else 1.0
            entries.append({"key": view["key"], "received": max(0.0, round(view["expected"] - missing / per_pack, 4))})
        inventory.lock_items(db, [i for i in [order.inventory_item_id, *[l.inventory_item_id for l in order.lines]] if i])
        try:
            _, summary = receiving.post_receipt(db, order, entries, client_key=f"voice:{command.id}", received_by=user.email, note="Checked in by voice")
        except receiving.ReceiptError as err:
            db.rollback()
            raise HTTPException(409, str(err)) from err
        done = ("Delivery checked in." if not summary["owed"] else f"Checked in, {summary['owed']} line{'s' if summary['owed'] != 1 else ''} short. A message to the rep is drafted.")
        parsed["claim_draft_id"] = summary.get("claim_draft_id")
    elif command.intent in ("ask", "navigate"):
        done = "Okay."  # the page asks the assistant or opens the page; nothing is written here
    else:
        raise HTTPException(422, "I didn't catch a command in that.")
    parsed["done"] = done
    command.parsed, command.status, command.confirmed_at = parsed, "confirmed", datetime.now(timezone.utc)
    db.commit()
    return {**_out(db, command), "done": done}


@router.post("/commands/{command_id}/discard")
def discard(command_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    command = db.query(VoiceCommand).filter(VoiceCommand.restaurant_id == _scope(db, user), VoiceCommand.id == command_id).first()
    if command is None:
        raise HTTPException(404, "Nothing previewed under that id")
    if command.status == "previewed":
        command.status = "discarded"
        db.commit()
    return {"id": command.id, "status": command.status}


@router.post("/aliases", status_code=201)
def learn_alias(body: AliasIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Remember what this restaurant calls an item out loud, from a correction on the preview."""
    restaurant_id = _scope(db, user)
    item = db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id, InventoryItem.id == body.inventory_item_id).first()
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    phrase = " ".join(body.phrase.lower().split())[:120]
    row = db.query(SpokenAlias).filter(SpokenAlias.restaurant_id == restaurant_id, SpokenAlias.phrase == phrase).first()
    if row is None:
        row = SpokenAlias(restaurant_id=restaurant_id, phrase=phrase, inventory_item_id=item.id)
        db.add(row)
    else:
        row.inventory_item_id = item.id
    db.commit()
    return {"phrase": phrase, "inventory_item_id": item.id, "name": item.name}


@router.post("/transcribe")
def transcribe(body: TranscribeIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Server-side speech to text, for browsers that can record but not recognise. Metered per restaurant.
    This site has no speech service connected, so after the budget check it says so; nothing is charged."""
    restaurant_id = _scope(db, user)
    if not notify.within_budget(db, restaurant_id, "voice_seconds"):
        raise HTTPException(402, "This month's voice minutes are used up. Typing still works.")
    raise HTTPException(503, "No speech service is connected to this site. Use a browser with voice typing, or type what you would say.")
