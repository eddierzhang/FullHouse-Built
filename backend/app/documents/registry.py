"""What becomes of a document once it is read.

    documents.registry.process(db, document_id)

Classify it (a model, or rules), read it with the schema for its kind, and make what it is:

- invoice: the same reader and checks as an upload; an invoice, questions, the digest.
- price_list: each line matched to an item the way invoice lines are; a confirmed match becomes an
  `agreed_price` proposal (cap 10 a week). Approval writes `agreed_prices`.
- recipe: lines matched to items; one `recipe` proposal (cap 5 a week) with the unmatched
  ingredients as its question. Approval writes `recipes`.
- license, permit, inspection, warranty: read and kept with their dates; Phase D turns them into
  calendar items.

Every field carries a confidence. Classification under 0.9, or a read under 0.8 or failing
validation, leaves the document in review for a person to type or confirm.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app import storage
from app.documents import structured
from app.documents.models import COMPLETE, EXTRACTED, NEEDS_REVIEW, Document

logger = logging.getLogger(__name__)

CLASSIFY_THRESHOLD = 0.9
FIELD_THRESHOLD = 0.8
COMPLIANCE_TYPES = ("license", "permit", "inspection", "warranty")


def process(db: Session, document_id: str, today: date | None = None, doc_type: str | None = None) -> Document | None:
    """Read one document and make what it is. `doc_type` is the reviewer's choice when the classifier wasn't sure."""
    document = db.get(Document, document_id)
    if document is None or not document.file_key:
        return document
    if document.status == COMPLETE and doc_type is None:
        return document
    data = storage.get(document.file_key)
    if data is None:
        document.status = NEEDS_REVIEW
        document.note = f"{document.note or ''} File missing.".strip()
        return document
    media = document.media_type or "application/pdf"
    if doc_type:
        document.doc_type, document.type_confidence = doc_type, 1.0
    elif document.type_confidence is None or document.doc_type in (None, "other"):
        kind, confidence, title = structured.classify(media, data)
        document.doc_type, document.type_confidence = kind, confidence
        if title and not document.note:
            document.note = title
        if confidence < CLASSIFY_THRESHOLD:
            document.status = NEEDS_REVIEW
            document.extracted = {**document.extracted, "classification": {"doc_type": kind, "confidence": confidence}}
            db.flush()
            return document
    handler = HANDLERS.get(document.doc_type)
    if handler is None:
        document.status = NEEDS_REVIEW
        db.flush()
        return document
    try:
        handler(db, document, data, media, today or date.today())
    except structured.ReadError as err:
        document.status = NEEDS_REVIEW
        document.extracted = {**document.extracted, "error": str(err)}
        document.note = f"{document.note or ''} Couldn't be read: {err}".strip()
    db.flush()
    from app.platform import events

    events.emit(db, document.restaurant_id, "document.processed", "documents", document.id,
                {"doc_type": document.doc_type, "status": document.status})
    return document


# --- invoices ---------------------------------------------------------------------------------


def _invoice(db: Session, document: Document, data: bytes, media: str, today: date) -> None:
    from app.prices import engine, extract
    from app.restaurant.models import Supplier

    try:
        if media != "application/pdf" and not extract.reads_photos():
            raise extract.ReaderError("This site reads PDFs only.")
        read = extract.read(media, data)
    except extract.ReaderError as exc:
        raise structured.ReadError(str(exc)) from exc
    document.extracted = read.model_dump(mode="json")
    document.confidence = min([line.confidence for line in read.lines if line.confidence is not None] or [1.0])
    if document.result_id:
        document.status = NEEDS_REVIEW if read.needs_review else COMPLETE
        return
    name = (read.supplier_guess or "").strip() or "Unknown supplier"
    supplier = db.query(Supplier).filter(Supplier.restaurant_id == document.restaurant_id, Supplier.name.ilike(name)).first()
    if supplier is None:
        supplier = Supplier(restaurant_id=document.restaurant_id, name=name[:255])
        db.add(supplier)
        db.flush()
    invoice, _ = engine.ingest_invoice(
        db, document.restaurant_id, supplier, read.date_guess or today, read.lines, source=document.source if document.source in ("email", "pdf") else "pdf",
        reference=read.reference, total=read.total, today=today, external_id=document.external_id,
    )
    invoice.document_id = document.id
    document.result_table, document.result_id = "supplier_invoices", invoice.id
    document.status = NEEDS_REVIEW if (read.needs_review or invoice.status == engine.NEEDS_REVIEW) else COMPLETE
    engine.build_digest(db, document.restaurant_id, today)


# --- price lists -------------------------------------------------------------------------------


def _price_list(db: Session, document: Document, data: bytes, media: str, today: date) -> None:
    from app.agents import caps
    from app.prices import engine
    from app.prices.models import ItemAlias, PriceItem
    from app.restaurant.models import Supplier

    recorded, reader = structured.read(media, data, structured.PRICE_LIST_TOOL, structured.PRICE_LIST_PROMPT, structured.price_list_by_rule)
    document.extracted = {**recorded, "reader": reader}
    lines = recorded.get("lines") or []
    if not lines:
        raise structured.ReadError("No priced lines were found on it.")
    confidences = [float(l.get("confidence", 1)) for l in lines] + [float(recorded.get("supplier_confidence", 1))]
    document.confidence = min(confidences)
    name = (recorded.get("supplier") or "").strip()
    supplier = db.query(Supplier).filter(Supplier.restaurant_id == document.restaurant_id, Supplier.name.ilike(name)).first() if name else None
    if supplier is None:
        # The reviewer names the supplier: an agreed price with no supplier means nothing.
        document.status = NEEDS_REVIEW
        document.note = f"{document.note or ''} Which supplier is this price list from?".strip()
        return
    effective = structured.as_date(recorded.get("effective_date")) or today
    aliases = {a.description_key: a for a in db.query(ItemAlias).filter(ItemAlias.restaurant_id == document.restaurant_id,
                                                                        ItemAlias.supplier_id == supplier.id)}
    proposed, unmatched, low = 0, [], []
    for line in lines:
        description = str(line.get("raw_description") or "").strip()
        price = line.get("unit_price")
        if not description or price is None:
            continue
        if float(line.get("confidence", 1)) < FIELD_THRESHOLD:
            low.append(description)
            continue
        pack = engine.parse_pack(description, line.get("pack_text"))
        alias = aliases.get(engine.normalize(description))
        item = db.get(PriceItem, alias.item_id) if alias else None
        pack_size = (alias.pack_size if alias and alias.pack_size else None) or (pack[0] if pack else None)
        if item is None:
            candidate, score = engine.find_candidate(db, document.restaurant_id, description)
            if candidate is not None and score >= 0.6:
                item, pack_size = candidate, pack_size or candidate.pack_size
        if item is None or not pack_size:
            unmatched.append(description)
            continue
        per_unit = round(float(price) / float(pack_size), 4)
        if propose_agreed_price(db, document.restaurant_id, supplier, item, per_unit, effective, document.id,
                                cited=f"{description} at {engine.money(float(price))} per {line.get('pack_text') or item.pack_unit}", today=today):
            proposed += 1
    document.extracted = {**document.extracted, "proposed": proposed, "unmatched": unmatched, "low_confidence": low}
    document.status = NEEDS_REVIEW if (unmatched or low) and not proposed else EXTRACTED if (unmatched or low) else COMPLETE
    document.result_table = "agent_actions"


def propose_agreed_price(db: Session, restaurant_id: str, supplier, item, per_unit: float, effective: date,
                         document_id: str | None, cited: str, today: date | None = None, line_id: str | None = None) -> bool:
    """One `agreed_price` proposal, within the weekly cap and not twice for the same item, supplier and price."""
    from app.agents import caps
    from app.agents.crud import get_definition_by_key
    from app.agents.definitions import AGENT_KEY
    from app.agents.models import AgentAction
    from app.prices import engine

    pending = db.query(AgentAction).filter(
        AgentAction.restaurant_id == restaurant_id, AgentAction.action_type == "agreed_price", AgentAction.status == "pending",
    ).all()
    for action in pending:
        p = action.payload or {}
        if p.get("item_id") == item.id and p.get("supplier_id") == supplier.id and abs(float(p.get("unit_price", 0)) - per_unit) < 1e-6:
            return False
    if not caps.within(db, restaurant_id, "agreed_price", today):
        return False
    definition = get_definition_by_key(db, AGENT_KEY)
    run = _system_run(db, restaurant_id, definition, f"Agreed price read from {'a price list' if document_id else 'an invoice line'}: {item.name}")
    db.add(AgentAction(restaurant_id=restaurant_id, run_id=run.id, agent_definition_id=run.agent_definition_id, action_type="agreed_price", payload={
        "item_id": item.id, "item_name": item.name, "supplier_id": supplier.id, "supplier_name": supplier.name,
        "unit_price": per_unit, "base_unit": item.unit, "per_pack": engine.per_pack(item, per_unit), "pack_sentence": f"{engine.unit_words(item.pack_size, item.unit)} per {item.pack_unit}",
        "effective_from": effective.isoformat(), "source_document_id": document_id, "source_line_id": line_id, "cited": cited,
        "note": f"{supplier.name} lists {item.name} at {engine.money(engine.per_pack(item, per_unit))} a {item.pack_unit} "
                f"({engine.money(per_unit)} per {item.unit}) from {engine.short_date(effective)}. Cited: {cited}.",
    }))
    db.flush()
    return True


def _system_run(db: Session, restaurant_id: str, definition, text: str):
    from datetime import datetime, timezone

    from app.agents import crud as agent_crud
    from app.agents.models import AgentRun

    now = datetime.now(timezone.utc)
    run = AgentRun(restaurant_id=restaurant_id, agent_definition_id=definition.id, status="succeeded", trigger_type="scheduled",
                   input=text, output_summary=text, started_at=now, finished_at=now, cost_usd=0.0)
    db.add(run)
    db.flush()
    for kind, payload in (("status_change", {"status": "running"}), ("log", {"text": text}), ("status_change", {"status": "succeeded"})):
        agent_crud.add_event(db, run.id, kind, payload)
    return run


# --- recipes ----------------------------------------------------------------------------------


def _recipe(db: Session, document: Document, data: bytes, media: str, today: date) -> None:
    from app.recipes import engine as recipes

    recorded, reader = structured.read(media, data, structured.RECIPE_TOOL, structured.RECIPE_PROMPT, structured.recipe_by_rule)
    document.extracted = {**recorded, "reader": reader}
    document.confidence = float(recorded.get("confidence") or 0)
    lines = recorded.get("lines") or []
    if not lines or document.confidence < FIELD_THRESHOLD:
        raise structured.ReadError("The recipe's lines couldn't be read clearly enough.")
    name = (recorded.get("name") or document.filename or "Recipe").strip()[:200]
    action = recipes.propose_recipe(db, document.restaurant_id, name=name, yield_qty=float(recorded.get("yield_qty") or 1),
                                    yield_unit=str(recorded.get("yield_unit") or "plate")[:32], ingredients=[
                                        {"text": str(l.get("ingredient_text") or ""), "qty": l.get("qty"), "unit": l.get("unit")} for l in lines
                                    ], source="document", document_id=document.id, today=today)
    if action is None:
        document.status = EXTRACTED
        document.note = f"{document.note or ''} Held: five recipe proposals already this week.".strip()
        return
    document.result_table, document.result_id = "agent_actions", action.id
    document.status = COMPLETE


# --- licenses, permits, inspections, warranties ---------------------------------------------------


def _compliance(db: Session, document: Document, data: bytes, media: str, today: date) -> None:
    recorded, reader = structured.read(media, data, structured.COMPLIANCE_TOOL, structured.COMPLIANCE_PROMPT, structured.compliance_by_rule)
    document.extracted = {**recorded, "reader": reader}
    document.confidence = float(recorded.get("confidence") or 0)
    if document.confidence < FIELD_THRESHOLD or not recorded.get("expires_on"):
        document.status = NEEDS_REVIEW
        document.note = f"{document.note or ''} The expiry date wasn't clear; set it.".strip()
        return
    document.status = EXTRACTED
    try:
        from app.calendar import engine as calendar  # Phase D

        calendar.propose_from_document(db, document, today)
    except ImportError:
        pass


HANDLERS = {
    "invoice": _invoice,
    "price_list": _price_list,
    "recipe": _recipe,
    **{kind: _compliance for kind in COMPLIANCE_TYPES},
}
