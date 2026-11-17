"""Autopilot: orders that go out by themselves, inside limits an owner set for that supplier.

The agent never sends anything, and nothing here is the agent. The flow is:

    draft     On a supplier's order day, shortly before its cutoff, one order is drafted as a proposal: the usual
              week from the invoices, brought to par where stock is tracked.
    decide    Every rule in app.ordering.policy is checked. All pass: the proposal is approved in the policy's
              name and its send is written to the outbox, to go after the cancel window. Any fail: it waits in
              the Inbox like any proposal, with the failed rules named.
    deliver   A job mails what is due. The outbox row is written in the same transaction as the approval, so a
              crash can delay an order but cannot lose or double one.
    cancel    Until it has gone, a person can stop it; the order stays as a draft for them to send or drop.

Three sends in a row that fail or are cancelled switch autopilot off for that supplier and say so.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.agents import caps
from app.agents.appliers import ApplyError, apply_action
from app.agents.crud import mark_action_decided
from app.agents.models import AgentAction
from app.audit.context import Actor, actor_context
from app.config import settings
from app.notify import service as notify
from app.ordering import pars, policy
from app.ordering.models import (
    CANCELLED,
    FAILED,
    HELD,
    PASSED,
    SENT,
    AutopilotDecision,
    AutopilotPolicy,
    AutopilotSettings,
    Outbox,
    SupplierCalendar,
)
from app.platform import flags
from app.prices import engine as prices
from app.prices import mail
from app.prices.models import Invoice, InvoiceLine, SupplierRep
from app.restaurant import orders, po_draft, po_mail
from app.restaurant.models import DRAFT, OPEN_ORDER_STATUSES, InventoryItem, PurchaseOrder, Restaurant, Supplier
from app.sales.models import SalesItem

logger = logging.getLogger(__name__)

AUTOPILOT = "autopilot"
#: How long before a supplier's cutoff its order is drafted.
DRAFT_LEAD_MINUTES = 180
FAILURES_BEFORE_OFF = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- settings -----------------------------------------------------------------------------------


def calendar_for(db: Session, restaurant_id: str, supplier_id: str) -> SupplierCalendar | None:
    return db.query(SupplierCalendar).filter(SupplierCalendar.restaurant_id == restaurant_id, SupplierCalendar.supplier_id == supplier_id).first()


def policy_for(db: Session, restaurant_id: str, supplier_id: str) -> AutopilotPolicy | None:
    return db.query(AutopilotPolicy).filter(AutopilotPolicy.restaurant_id == restaurant_id, AutopilotPolicy.supplier_id == supplier_id).first()


def is_paused(db: Session, restaurant_id: str) -> bool:
    row = db.query(AutopilotSettings).filter(AutopilotSettings.restaurant_id == restaurant_id).first()
    return bool(row and row.paused)


def set_paused(db: Session, restaurant_id: str, paused: bool, by: str | None) -> None:
    row = db.query(AutopilotSettings).filter(AutopilotSettings.restaurant_id == restaurant_id).first()
    if row is None:
        row = AutopilotSettings(restaurant_id=restaurant_id)
        db.add(row)
    row.paused, row.paused_by, row.paused_at = paused, (by if paused else None), (_now() if paused else None)
    db.flush()


def order_email(db: Session, restaurant_id: str, supplier_id: str, calendar: SupplierCalendar | None = None) -> str | None:
    """Where this supplier's orders go: the address on its calendar, else the rep the drafts remember."""
    calendar = calendar or calendar_for(db, restaurant_id, supplier_id)
    if calendar and calendar.order_email:
        return calendar.order_email
    rep = db.query(SupplierRep).filter(SupplierRep.restaurant_id == restaurant_id, SupplierRep.supplier_id == supplier_id).first()
    return rep.email if rep and rep.email else None


def limits_of(row: AutopilotPolicy | None, paused: bool) -> policy.Limits:
    if row is None:
        return policy.Limits(enabled=False, paused=paused)
    return policy.Limits(enabled=row.enabled, paused=paused, per_order_cap=row.per_order_cap, weekly_cap=row.weekly_cap,
                         max_price_rise_pct=row.max_price_rise_pct, max_qty_multiple=row.max_qty_multiple, min_history=row.min_history)


# --- the facts a decision rests on --------------------------------------------------------------


def line_facts(db: Session, restaurant_id: str, supplier_id: str, lines: list[dict], today: date) -> tuple[policy.DraftLine, ...]:
    facts = prices.gather(db, restaurant_id, today)
    out = []
    for line in lines:
        held = facts.get(line.get("item_id") or "")
        mine = sorted((p for p in (held.priced if held else []) if p.supplier_id == supplier_id), key=lambda p: (p.day, p.line.line_no))
        by_day: dict[date, float] = {}
        for priced in mine:
            by_day[priced.day] = prices.per_pack(held.item, priced.per_unit)
        days = sorted(by_day)
        median = line.get("median_weekly_packs")
        if median is None:
            weeks = [sum(p.line.qty for p in mine if today - timedelta(days=7 * (i + 1)) < p.day <= today - timedelta(days=7 * i))
                     for i in range(prices.WINDOW_WEEKS)]
            median = statistics.median(weeks) if weeks else 0
        out.append(policy.DraftLine(description=str(line.get("description") or "Item"), qty=float(line.get("qty") or 0),
                                    unit_price=line.get("unit_price_expected"), times_bought=len(days), median_qty=float(median or 0),
                                    previous_price=by_day[days[-2]] if len(days) >= 2 else None))
    return tuple(out)


def stock_state(db: Session, restaurant_id: str, today: date) -> tuple[bool, str]:
    """Whether the figures an order rests on are current. With stock tracking on, sales must be in through yesterday."""
    if not flags.enabled(db, restaurant_id, flags.V3_STOCK):
        return True, "Quantities come from your invoice history; stock tracking is off."
    from sqlalchemy import func

    last = db.query(func.max(SalesItem.date)).filter(SalesItem.restaurant_id == restaurant_id).scalar()
    if last is None:
        return False, "No sales have come in from the point of sale, so the stock figures can't be trusted."
    if last < today - timedelta(days=1):
        return False, f"Sales are only in through {last.strftime('%b %-d')}, so the stock figures are out of date."
    return True, f"Sales are in through {last.strftime('%b %-d')}."


def waiting_for(db: Session, restaurant_id: str, supplier_id: str) -> list[AutopilotDecision]:
    """Orders for this supplier that passed and are waiting out their cancel window."""
    return db.query(AutopilotDecision).filter(AutopilotDecision.restaurant_id == restaurant_id, AutopilotDecision.supplier_id == supplier_id,
                                              AutopilotDecision.outcome == PASSED).all()


def sent_this_week(db: Session, restaurant_id: str, supplier_id: str, now: datetime) -> float:
    """What autopilot has committed to this supplier in the last seven days: orders sent, and orders that have
    passed and are waiting to send. Counting only what has gone would let two orders through one weekly cap."""
    rows = db.query(PurchaseOrder.estimated_cost).filter(
        PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.supplier_id == supplier_id, PurchaseOrder.autopilot.is_(True),
        PurchaseOrder.sent_at.isnot(None), PurchaseOrder.sent_at >= now - timedelta(days=7))
    waiting = sum(float(d.total or 0) for d in waiting_for(db, restaurant_id, supplier_id))
    return round(sum(float(cost or 0) for (cost,) in rows) + waiting, 2)


def draft_of(db: Session, action: AgentAction, now: datetime, today: date) -> policy.Draft:
    payload = action.payload or {}
    restaurant_id, supplier_id = action.restaurant_id, payload.get("supplier_id")
    calendar = calendar_for(db, restaurant_id, supplier_id)
    lines = list(payload.get("lines") or [])
    total = payload.get("expected_total")
    if total is None:
        total = sum(float(l.get("qty") or 0) * float(l.get("unit_price_expected") or 0) for l in lines)
    fresh, detail = stock_state(db, restaurant_id, today)
    return policy.Draft(
        supplier_name=str(payload.get("supplier_name") or "the supplier"), total=round(float(total), 2),
        lines=line_facts(db, restaurant_id, supplier_id, lines, today), channel=calendar.channel if calendar else "email",
        order_email=order_email(db, restaurant_id, supplier_id, calendar), sent_this_week=sent_this_week(db, restaurant_id, supplier_id, now),
        stock_fresh=fresh, stock_detail=detail, can_send=mail.can_send() or settings.demo_mode)


# --- drafting on order days ---------------------------------------------------------------------


def _local(db: Session, restaurant_id: str, moment: datetime) -> datetime:
    restaurant = db.get(Restaurant, restaurant_id)
    try:
        return moment.astimezone(ZoneInfo((restaurant.timezone if restaurant else None) or "UTC"))
    except Exception:
        return moment


def _on_order(db: Session, restaurant_id: str) -> dict[str, float]:
    """Units already ordered and not yet in, by stock item."""
    out: dict[str, float] = {}
    for order in db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.status.in_(OPEN_ORDER_STATUSES)):
        if order.inventory_item_id:
            out[order.inventory_item_id] = out.get(order.inventory_item_id, 0.0) + order.quantity
        for line in order.lines:
            if line.inventory_item_id and line.received_qty is None and line.units:
                out[line.inventory_item_id] = out.get(line.inventory_item_id, 0.0) + line.units
    return out


def next_delivery(calendar: SupplierCalendar | None, supplier: Supplier, today: date) -> date:
    lead = (calendar.lead_time_days if calendar and calendar.lead_time_days is not None else supplier.lead_time_days) or orders.DEFAULT_LEAD_DAYS
    day = today + timedelta(days=lead)
    delivery_days = set(calendar.delivery_days or []) if calendar else set()
    for _ in range(7):
        if not delivery_days or day.weekday() in delivery_days:
            return day
        day += timedelta(days=1)
    return day


def order_lines(db: Session, restaurant_id: str, supplier: Supplier, today: date) -> list[dict]:
    """The supplier's usual week from the invoices, with each stocked line brought to its par in whole packs."""
    lines = po_draft.lines_for(db, restaurant_id, supplier, today)
    track = flags.enabled(db, restaurant_id, flags.V3_STOCK)
    on_order = _on_order(db, restaurant_id) if track else {}
    out = []
    for line in lines:
        stock_id, units = line.get("inventory_item_id"), line.get("units")
        item = db.get(InventoryItem, stock_id) if (track and stock_id and units) else None
        if item is not None and item.par is not None and line["qty"]:
            packs = pars.packs_to_par(item.par, item.quantity_on_hand, on_order.get(item.id, 0.0), units / line["qty"])
            if packs == 0:
                continue  # at par already
            line = {**line, "units": round(units / line["qty"] * packs, 4), "qty": packs, "basis": "par"}
        else:
            line = {**line, "basis": "usual"}
        out.append(line)
    return out


def draft_for(db: Session, restaurant_id: str, supplier: Supplier, today: date) -> AgentAction | None:
    """One drafted order for this supplier, as a proposal. None when one is already waiting, there is nothing
    to order, it is under the supplier's minimum, or the weekly cap on proposals is reached."""
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "purchase_order",
                                               AgentAction.status == "pending"):
        held = action.payload or {}
        # A whole-supplier draft already waiting; a single-item reorder for the same supplier is a different thing.
        if held.get("supplier_id") == supplier.id and held.get("source") == "po_draft" and len(held.get("lines") or []) > 0:
            return None
    if waiting_for(db, restaurant_id, supplier.id):
        return None  # one order at a time: the last one has not gone yet
    lines = order_lines(db, restaurant_id, supplier, today)
    if not lines:
        return None
    calendar = calendar_for(db, restaurant_id, supplier.id)
    total = round(sum(l["qty"] * (l["unit_price_expected"] or 0) for l in lines), 2)
    if calendar and calendar.minimum_dollars and total < calendar.minimum_dollars:
        notify.tell(db, restaurant_id, kind="order_below_minimum", title=f"{supplier.name}: this order is under their minimum",
                    body=f"{prices.money(total)} against a {prices.money(calendar.minimum_dollars)} minimum. Held until the next order day.",
                    link="#autopilot", dedupe_key=f"minimum:{supplier.id}:{today.isoformat()}")
        return None
    watched = policy_for(db, restaurant_id, supplier.id)
    # Orders that go out by themselves don't crowd the owner's Inbox, so they aren't counted against its weekly cap.
    if not (watched and watched.enabled) and not caps.within(db, restaurant_id, "purchase_order", today):
        return None
    expected = next_delivery(calendar, supplier, today)
    to_par = sum(1 for l in lines if l.get("basis") == "par")
    rationale = (f"{supplier.name}, for delivery {expected.strftime('%A %b %-d')}: "
                 + "; ".join(f"{l['description']} {l['qty']:g} {l['unit']} at {prices.money(l['unit_price_expected'] or 0)}" for l in lines[:6])
                 + (f"; and {len(lines) - 6} more" if len(lines) > 6 else "") + f". About {prices.money(total)}. "
                 + (f"{to_par} of {len(lines)} lines bring stock up to par; the rest are your usual week." if to_par else "Your usual week from the invoices."))
    run = po_draft._system_run(db, restaurant_id, f"Order draft for {supplier.name}")
    action = AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="purchase_order", payload={
        "supplier_id": supplier.id, "supplier_name": supplier.name, "item_name": f"{len(lines)} items", "quantity": sum(l["qty"] for l in lines),
        "lines": lines, "expected_total": total, "note": rationale, "source": "po_draft", "expected_on": expected.isoformat(),
        # Only an order drafted here, on the supplier's order day, is ever decided by the policy (see `decide`).
        "drafted_by": AUTOPILOT,
        "cited_line_ids": [i for l in lines for i in l.get("cited_line_ids", [])][:40],
    })
    db.add(action)
    db.flush()
    return action


def drafts_due(db: Session, restaurant_id: str, now: datetime) -> list[AgentAction]:
    """Draft for every supplier whose order day is today and whose cutoff is within the next three hours."""
    local = _local(db, restaurant_id, now)
    made = []
    for calendar in db.query(SupplierCalendar).filter(SupplierCalendar.restaurant_id == restaurant_id):
        if local.weekday() not in set(calendar.order_days or []):
            continue
        try:
            hour, minute = (int(part) for part in calendar.cutoff_time.split(":"))
        except ValueError:
            continue
        cutoff = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not (cutoff - timedelta(minutes=DRAFT_LEAD_MINUTES) <= local < cutoff):
            continue
        supplier = db.query(Supplier).filter(Supplier.restaurant_id == restaurant_id, Supplier.id == calendar.supplier_id).first()
        placed_today = db.query(PurchaseOrder.id).filter(
            PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.supplier_id == calendar.supplier_id,
            PurchaseOrder.ordered_at >= now - timedelta(hours=18)).first()
        if supplier is None or placed_today:
            continue
        action = draft_for(db, restaurant_id, supplier, local.date())
        if action is not None:
            made.append(action)
    return made


# --- deciding -----------------------------------------------------------------------------------


def decide(db: Session, action: AgentAction, now: datetime | None = None, today: date | None = None) -> AutopilotDecision | None:
    """Check one drafted order against its supplier's limits. None when autopilot is not on for that supplier,
    or the order has been looked at already: then it is an ordinary proposal for a person."""
    now = now or _now()
    payload = action.payload or {}
    restaurant_id, supplier_id = action.restaurant_id, payload.get("supplier_id")
    if action.status != "pending" or action.action_type != "purchase_order" or not supplier_id or not payload.get("lines"):
        return None
    # Only the order this module drafted for the supplier's order day. An order the agent proposed because
    # someone asked it to, or the weekly "usual order" draft, is a proposal for a person like any other: if
    # those could go out by themselves, anyone who can talk to the agent could place an order.
    if payload.get("drafted_by") != AUTOPILOT:
        return None
    row = policy_for(db, restaurant_id, supplier_id)
    if row is None or not row.enabled:
        return None
    if db.query(AutopilotDecision.id).filter(AutopilotDecision.restaurant_id == restaurant_id, AutopilotDecision.po_id == action.id).first():
        return None
    today = today or _local(db, restaurant_id, now).date()
    draft = draft_of(db, action, now, today)
    verdict = policy.evaluate(draft, limits_of(row, is_paused(db, restaurant_id)))
    rules = [r.as_dict() for r in verdict.rules]
    if not verdict.passed:
        decision = AutopilotDecision(restaurant_id=restaurant_id, po_id=action.id, supplier_id=supplier_id, outcome=HELD, rules=rules,
                                     total=draft.total, decided_at=now)
        db.add(decision)
        why = [r.detail for r in verdict.failed]
        action.payload = {**payload, "autopilot_held": why,
                          "note": f"Autopilot held this for you: {' '.join(why)} " + str(payload.get("note") or "")}
        notify.tell(db, restaurant_id, kind="autopilot_held", title=f"{draft.supplier_name} order needs you",
                    body=" ".join(why), link="#approvals", dedupe_key=f"autopilot:held:{action.id}")
        db.flush()
        return decision
    attribution = Actor(type="system", id=AUTOPILOT, agent_run_id=action.run_id, agent_action_id=action.id, restaurant_id=restaurant_id)
    try:
        with actor_context(attribution):
            result = apply_action(db, action)
            mark_action_decided(db, action, "approved", applied_result=f"Approved under your autopilot limits for {draft.supplier_name}. {result}")
    except ApplyError as err:
        logger.warning("autopilot could not draft the order for action %s: %s", action.id, err)
        return None
    order = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == restaurant_id, PurchaseOrder.action_id == action.id).first()
    if order is None:
        return None
    order.autopilot = True
    if payload.get("expected_on"):
        order.expected_on = date.fromisoformat(payload["expected_on"])
    send_after = now + timedelta(minutes=row.cancel_window_minutes)
    decision = AutopilotDecision(restaurant_id=restaurant_id, po_id=order.id, supplier_id=supplier_id, outcome=PASSED, rules=rules,
                                 total=draft.total, send_after=send_after, decided_at=now)
    db.add(decision)
    db.add(Outbox(restaurant_id=restaurant_id, kind="po_email", dedupe_key=f"po:{order.id}", payload={"po_id": order.id, "to": draft.order_email},
                  not_before=send_after))
    when = _local(db, restaurant_id, send_after).strftime("%-I:%M %p").lower()
    notify.tell(db, restaurant_id, kind="autopilot_send", title=f"{draft.supplier_name} order goes out at {when}",
                body=f"{prices.money(draft.total)}, {len(draft.lines)} lines. Cancel it before then if it looks wrong.",
                link="#autopilot", dedupe_key=f"autopilot:send:{order.id}")
    db.flush()
    return decision


def _strike(db: Session, restaurant_id: str, supplier_id: str | None, supplier_name: str) -> None:
    """One more send that failed or was cancelled. The third in a row switches autopilot off for the supplier."""
    row = policy_for(db, restaurant_id, supplier_id) if supplier_id else None
    if row is None:
        return
    row.consecutive_failures = (row.consecutive_failures or 0) + 1
    if row.consecutive_failures >= FAILURES_BEFORE_OFF and row.enabled:
        row.enabled = False
        row.paused_reason = f"Switched off after {FAILURES_BEFORE_OFF} sends in a row failed or were cancelled."
        notify.tell(db, restaurant_id, kind="autopilot_off", title=f"Autopilot is off for {supplier_name}", body=row.paused_reason,
                    link="#autopilot", dedupe_key=f"autopilot:off:{supplier_id}:{_now().date().isoformat()}")


def cancel(db: Session, decision: AutopilotDecision, by: str | None) -> AutopilotDecision:
    """Stop an order that has not gone yet. It stays as a draft, for a person to send or drop."""
    if decision.outcome != PASSED:
        raise ValueError("That order has already gone out, or was never going to." if decision.outcome == SENT else "There is nothing waiting to send.")
    row = db.query(Outbox).filter(Outbox.restaurant_id == decision.restaurant_id, Outbox.dedupe_key == f"po:{decision.po_id}").first()
    if row is not None and row.status == "pending":
        row.status = "cancelled"
    decision.outcome, decision.cancelled_by = CANCELLED, by
    order = db.get(PurchaseOrder, decision.po_id)
    _strike(db, decision.restaurant_id, decision.supplier_id, (order.supplier_name if order else None) or "this supplier")
    db.flush()
    return decision


# --- delivering ---------------------------------------------------------------------------------


def deliver_due(db: Session, now: datetime | None = None, restaurant_id: str | None = None) -> int:
    """Mail every order whose cancel window has closed. Returns how many went."""
    now = now or _now()
    query = db.query(Outbox).filter(Outbox.kind == "po_email", Outbox.status == "pending", Outbox.not_before <= now)
    if restaurant_id:
        query = query.filter(Outbox.restaurant_id == restaurant_id)
    sent = 0
    for row in query.order_by(Outbox.not_before).all():
        order = db.query(PurchaseOrder).filter(PurchaseOrder.restaurant_id == row.restaurant_id, PurchaseOrder.id == row.payload.get("po_id")).first()
        decision = db.query(AutopilotDecision).filter(AutopilotDecision.restaurant_id == row.restaurant_id, AutopilotDecision.po_id == row.payload.get("po_id")).first()
        if order is None or order.status != DRAFT or decision is None or decision.outcome != PASSED:
            row.status = "cancelled"  # sent or dropped by a person in the meantime
            continue
        name = order.supplier_name or "the supplier"
        limits = limits_of(policy_for(db, row.restaurant_id, order.supplier_id), is_paused(db, row.restaurant_id))
        if limits.paused or not limits.enabled:
            row.status, decision.outcome = "cancelled", HELD
            decision.rules = [*decision.rules, {"rule": "not_paused" if limits.paused else "autopilot_on", "ok": False,
                                                "detail": "Autopilot was paused before this went out." if limits.paused
                                                else f"Autopilot was switched off for {name} before this went out."}]
            notify.tell(db, row.restaurant_id, kind="autopilot_held", title=f"{name} order was not sent",
                        body="Autopilot was paused or switched off first. The order is a draft under On order.", link="#autopilot",
                        dedupe_key=f"autopilot:unsent:{order.id}")
            continue
        row.attempts += 1
        try:
            delivered = po_mail.send(db, order, [row.payload["to"]], db.get(Restaurant, row.restaurant_id))
        except Exception as err:  # the mail provider said no: a failed send, not a crashed job
            delivered, row.last_error = False, str(err)[:500]
        if delivered or (settings.demo_mode and not mail.can_send()):
            orders.mark_sent(db, order, sent_by=AUTOPILOT)
            order.note = f"{order.note + '. ' if order.note else ''}Sent by autopilot to {row.payload['to']}."[:255]
            row.status, row.sent_at = "sent", now
            decision.outcome, decision.sent_at = SENT, now
            watched = policy_for(db, row.restaurant_id, order.supplier_id)
            if watched is not None:
                watched.consecutive_failures = 0
            notify.tell(db, row.restaurant_id, kind="autopilot_sent", title=f"{name} order sent",
                        body=f"{prices.money(decision.total)}, expected {order.expected_on.strftime('%A %b %-d') if order.expected_on else 'soon'}.",
                        link="#autopilot", dedupe_key=f"autopilot:sent:{order.id}")
            sent += 1
        else:
            row.status, decision.outcome = "failed", FAILED
            row.last_error = row.last_error or "The mail could not be delivered."
            notify.tell(db, row.restaurant_id, kind="autopilot_failed", title=f"{name} order could not be sent",
                        body="The order is a draft under On order. Send it yourself, or place it by phone.", link="#autopilot",
                        dedupe_key=f"autopilot:failed:{order.id}")
            _strike(db, row.restaurant_id, order.supplier_id, name)
    db.flush()
    return sent


def run(db: Session, restaurant_id: str, now: datetime | None = None) -> dict:
    """The autopilot job: pars, any drafts due, a decision on each waiting draft, and whatever is due to send."""
    now = now or _now()
    changed = pars.recompute(db, restaurant_id)
    drafted = drafts_due(db, restaurant_id, now)
    decided = []
    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "purchase_order",
                                               AgentAction.status == "pending").all():
        decision = decide(db, action, now)
        if decision is not None:
            decided.append(decision.outcome)
    return {"pars": changed, "drafted": len(drafted), "passed": decided.count(PASSED), "held": decided.count(HELD),
            "sent": deliver_due(db, now, restaurant_id)}


# --- the preview --------------------------------------------------------------------------------


def preview(db: Session, restaurant_id: str, supplier: Supplier, limits: policy.Limits, today: date | None = None) -> dict:
    """What these limits would have done with this supplier's last eight weeks: each invoice replayed as the
    order it was, against only what was known before it."""
    today = today or date.today()
    since = today - timedelta(weeks=prices.WINDOW_WEEKS)
    earlier = today - timedelta(weeks=2 * prices.WINDOW_WEEKS)
    invoices = (db.query(Invoice).filter(Invoice.restaurant_id == restaurant_id, Invoice.supplier_id == supplier.id,
                                         Invoice.status == prices.COMPLETE, Invoice.invoice_date > earlier, Invoice.invoice_date <= today)
                .order_by(Invoice.invoice_date, Invoice.id).all())
    history: dict[str, list[tuple[date, float, float]]] = {}  # item -> [(day, packs, price a pack)]
    rows, sends = [], []
    for invoice in invoices:
        lines = db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice.id, InvoiceLine.item_id.isnot(None)).order_by(InvoiceLine.line_no).all()
        if invoice.invoice_date > since and lines:
            window = invoice.invoice_date - timedelta(weeks=prices.WINDOW_WEEKS)
            draft_lines = []
            for line in lines:
                before = [h for h in history.get(line.item_id, []) if h[0] > window]
                draft_lines.append(policy.DraftLine(description=line.description, qty=line.qty, unit_price=line.unit_price, times_bought=len(before),
                                                    median_qty=statistics.median(h[1] for h in before) if before else 0.0,
                                                    previous_price=before[-1][2] if before else None))
            total = round(sum(l.extended for l in lines), 2)
            week = round(sum(amount for day, amount in sends if day > invoice.invoice_date - timedelta(days=7)), 2)
            verdict = policy.evaluate(policy.Draft(supplier_name=supplier.name, total=total, lines=tuple(draft_lines), order_email="preview@example.com",
                                                   sent_this_week=week), limits)
            if verdict.passed:
                sends.append((invoice.invoice_date, total))
            rows.append({"date": invoice.invoice_date, "reference": invoice.reference, "total": total, "would_send": verdict.passed,
                         "held_for": [r.detail for r in verdict.failed]})
        for line in lines:
            history.setdefault(line.item_id, []).append((invoice.invoice_date, line.qty, line.unit_price))
    return {"supplier_name": supplier.name, "weeks": prices.WINDOW_WEEKS, "orders": rows,
            "would_send": sum(1 for r in rows if r["would_send"]), "would_hold": sum(1 for r in rows if not r["would_send"]),
            "sent_total": round(sum(r["total"] for r in rows if r["would_send"]), 2)}
