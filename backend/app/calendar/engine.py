"""What is coming due, from the documents and the equipment register.

- `propose_from_document`: a read license, permit, inspection report or warranty becomes a
  `calendar_item` proposal (cap 10 a week); a warranty also proposes the asset it covers.
- `scan`, daily: items due within DUE_WINDOW days with no proof become "Coming due" cards in the
  digest; items past their date are marked overdue; equipment whose next service falls within
  SERVICE_WINDOW days gets a service item proposed.
- Attaching a proof document closes the item (and, for a recurring one, makes the next).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.agents import caps
from app.agents.models import AgentAction, AgentRun
from app.calendar.models import DONE, OPEN, OVERDUE, Asset, CalendarItem
from app.documents.models import Document
from app.prices.engine import money, short_date

DUE_WINDOW = 30
SERVICE_WINDOW = 14
DOC_KIND = {"license": "license", "permit": "permit", "inspection": "inspection", "warranty": "warranty"}


def _system_run(db: Session, restaurant_id: str, text: str) -> AgentRun:
    from app.restaurant.po_draft import _system_run as make

    return make(db, restaurant_id, text)


def _parse_term(text: str | None) -> int | None:
    """"12 months", "1 year", "90 days" -> days."""
    if not text:
        return None
    m = re.search(r"(\d+)\s*(year|yr|month|mo|day)s?", text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * 365 if unit.startswith("y") else n * 30 if unit.startswith("m") else n


def propose_from_document(db: Session, document: Document, today: date | None = None, run: AgentRun | None = None) -> list[AgentAction]:
    """Calendar (and asset) proposals from a read compliance document. Idempotent per document."""
    today = today or date.today()
    kind = DOC_KIND.get(document.doc_type)
    extracted = document.extracted or {}
    if kind is None:
        return []
    due = None
    if extracted.get("expires_on"):
        try:
            due = date.fromisoformat(str(extracted["expires_on"])[:10])
        except ValueError:
            due = None
    issued = None
    if extracted.get("issued_on"):
        try:
            issued = date.fromisoformat(str(extracted["issued_on"])[:10])
        except ValueError:
            issued = None
    if due is None and kind == "warranty" and issued:
        from app.documents.structured import add_term

        term = extracted.get("term") or extracted.get("title") or document.note or ""
        if _parse_term(term):
            due = add_term(issued, term)
    if due is None:
        return []
    existing = [a for a in db.query(AgentAction).filter(AgentAction.restaurant_id == document.restaurant_id,
                                                        AgentAction.action_type == "calendar_item", AgentAction.status != "rejected")
                if (a.payload or {}).get("source_document_id") == document.id]
    if existing:
        return existing
    title = (extracted.get("title") or f"{kind.title()} from {extracted.get('issuer') or 'the issuer'}").strip()[:200]
    made = []
    if not caps.within(db, document.restaurant_id, "calendar_item", today):
        return []
    run = run or _system_run(db, document.restaurant_id, f"{kind.title()} read: {title}")
    recurrence = "yearly" if kind in ("license", "permit", "inspection") else None
    payload = {
        "kind": kind, "title": title, "due_on": due.isoformat(), "recurrence": recurrence, "source_document_id": document.id,
        "issuer": extracted.get("issuer"), "item_name": title,
        "note": f"{title}{' from ' + extracted['issuer'] if extracted.get('issuer') else ''}, {'expires' if kind != 'inspection' else 'next due'} "
                f"{short_date(due)}. Read from {document.filename or 'the document'}.",
    }
    if kind == "warranty":
        payload["asset"] = {"name": (extracted.get("asset_name") or title)[:200], "vendor": extracted.get("vendor") or extracted.get("issuer"),
                            "purchased_on": issued.isoformat() if issued else None, "warranty_until": due.isoformat(),
                            "source_document_id": document.id}
        payload["note"] += " The equipment is added to the register with it."
    action = AgentAction(restaurant_id=document.restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id,
                         action_type="calendar_item", payload=payload)
    db.add(action)
    db.flush()
    made.append(action)
    return made


def write_item(db: Session, restaurant_id: str, payload: dict) -> CalendarItem:
    """The applier's work: the calendar item (and the asset, for a warranty)."""
    due = date.fromisoformat(str(payload["due_on"])[:10])
    asset_id = payload.get("asset_id")
    if payload.get("asset"):
        a = payload["asset"]
        asset = Asset(restaurant_id=restaurant_id, name=str(a.get("name") or payload.get("title") or "Equipment")[:200], vendor=(a.get("vendor") or None),
                      purchased_on=date.fromisoformat(a["purchased_on"]) if a.get("purchased_on") else None,
                      warranty_until=date.fromisoformat(a["warranty_until"]) if a.get("warranty_until") else due,
                      service_interval_days=a.get("service_interval_days"), source_document_id=a.get("source_document_id"))
        db.add(asset)
        db.flush()
        asset_id = asset.id
    item = CalendarItem(restaurant_id=restaurant_id, kind=str(payload.get("kind") or "license")[:16], title=str(payload.get("title") or "Dated item")[:200],
                        due_on=due, recurrence=payload.get("recurrence"), source_document_id=payload.get("source_document_id"),
                        status=OVERDUE if due < date.today() else OPEN, asset_id=asset_id)
    db.add(item)
    db.flush()
    return item


def attach_proof(db: Session, item: CalendarItem, document_id: str, today: date | None = None) -> CalendarItem | None:
    """Proof closes the item; a recurring one gets its next date."""
    today = today or date.today()
    item.proof_document_id = document_id
    item.status = DONE
    db.flush()
    nxt = None
    if item.recurrence:
        days = 365 if item.recurrence == "yearly" else 30 if item.recurrence == "monthly" else _parse_term(item.recurrence)
        if days:
            nxt = CalendarItem(restaurant_id=item.restaurant_id, kind=item.kind, title=item.title, due_on=item.due_on + timedelta(days=days),
                               recurrence=item.recurrence, source_document_id=document_id, asset_id=item.asset_id)
            db.add(nxt)
            db.flush()
    return nxt


def scan(db: Session, restaurant_id: str, today: date | None = None) -> dict:
    """Daily: overdue marks, service proposals for equipment coming due. The cards are built with the digest."""
    today = today or date.today()
    overdue = 0
    for item in db.query(CalendarItem).filter(CalendarItem.restaurant_id == restaurant_id, CalendarItem.status == OPEN):
        if item.due_on < today:
            item.status = OVERDUE
            overdue += 1
    proposed = 0
    pending_titles = {(a.payload or {}).get("title") for a in db.query(AgentAction).filter(
        AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "calendar_item", AgentAction.status == "pending")}
    for asset in db.query(Asset).filter(Asset.restaurant_id == restaurant_id, Asset.service_interval_days.isnot(None)):
        if not asset.last_service_on:
            continue
        due = asset.last_service_on + timedelta(days=asset.service_interval_days)
        if due > today + timedelta(days=SERVICE_WINDOW):
            continue
        title = f"Service: {asset.name}"
        if title in pending_titles or db.query(CalendarItem).filter(CalendarItem.restaurant_id == restaurant_id, CalendarItem.asset_id == asset.id,
                                                                     CalendarItem.kind == "service", CalendarItem.status.in_((OPEN, OVERDUE))).count():
            continue
        if not caps.within(db, restaurant_id, "calendar_item", today):
            break
        run = _system_run(db, restaurant_id, title)
        db.add(AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="calendar_item", payload={
            "kind": "service", "title": title, "due_on": due.isoformat(), "recurrence": f"{asset.service_interval_days} days", "asset_id": asset.id,
            "item_name": title, "note": f"{asset.name} was last serviced {short_date(asset.last_service_on)}; every {asset.service_interval_days} days puts the next on {short_date(due)}.",
        }))
        proposed += 1
    db.flush()
    from app.platform import events

    events.emit(db, restaurant_id, "calendar.scanned", "calendar_items", today.isoformat(), {"overdue": overdue, "proposed": proposed})
    return {"overdue": overdue, "proposed": proposed}


def due_cards(db: Session, restaurant_id: str | None, today: date | None = None) -> list[dict]:
    """"Coming due" cards: open items due within 30 days (or overdue) with no proof. Impact is the days left, as a
    sort key only; the section shows dates, not dollars."""
    today = today or date.today()
    cards = []
    horizon = today + timedelta(days=DUE_WINDOW)
    rows = (db.query(CalendarItem).filter(CalendarItem.restaurant_id == restaurant_id, CalendarItem.status.in_((OPEN, OVERDUE)),
                                          CalendarItem.proof_document_id.is_(None), CalendarItem.due_on <= horizon).order_by(CalendarItem.due_on).all())
    for item in rows:
        days = (item.due_on - today).days
        when = f"overdue by {-days} day{'s' if days != -1 else ''}" if days < 0 else "due today" if days == 0 else f"due in {days} day{'s' if days != 1 else ''}"
        sources = [{"kind": "document", "id": item.source_document_id, "label": "the document it came from"}] if item.source_document_id else []
        cards.append({
            "section": "due", "item_id": item.id, "calendar_item_id": item.id, "item_name": item.title,
            "impact": max(0.01, round(31 - min(days, 30), 2)),
            "sentence": f"{item.kind.replace('_', ' ').title()} {when}: {short_date(item.due_on)}. Attach the renewal or report as proof to clear it.",
            "action": {"kind": "calendar", "label": "Open the calendar"}, "sources": sources,
        })
    return cards
