"""The price-intelligence API. Routes and shapes: app/prices/schemas.py.

Owners, admins and members of a restaurant read everything here; writing
-- invoices, answers, drafts, hiding an item -- is for owners and admins,
except answering questions, which a member may do too (they unpack the
deliveries). A bookkeeper, who belongs to no restaurant, reaches only the
/bookkeeper routes, scoped to the clients linked to them.
"""

import html
import logging
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.agents import crud as agent_crud
from app.agents import executor
from app.agents.definitions import AGENT_KEY
from app.auth.deps import current_user, manager
from app.auth.models import BOOKKEEPER, User
from app.config import settings
from app.db.session import get_db
from app.prices import billcom, engine, extract, mail, schemas, stock
from app.prices.models import (
    ANSWERED,
    DISCARDED,
    DRAFT,
    OPEN,
    SENT,
    AgentQuestion,
    BookkeeperClient,
    Invoice,
    InvoiceLine,
    OutreachDraft,
    PriceDigest,
    PriceItem,
    ThreadMessage,
)
from app.restaurant import profile
from app.restaurant.inventory import scope_for
from app.restaurant.models import Restaurant, Supplier

router = APIRouter(prefix="/api/v1/prices", tags=["prices"])

logger = logging.getLogger(__name__)

#: How an agent run is told it's answering a thread question, so the scripted provider recognises it.
QUESTION_PREFIX = "Price question: "


def _scope(db: Session, user: User) -> str | None:
    if user.role == BOOKKEEPER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bookkeepers work from the clients list.")
    return scope_for(db, user)


def _supplier_ref(db: Session, supplier_id: str) -> schemas.SupplierRef:
    supplier = db.get(Supplier, supplier_id)
    return schemas.SupplierRef(id=supplier_id, name=supplier.name if supplier else "Supplier")


def question_out(db: Session, question: AgentQuestion) -> schemas.QuestionOut:
    line = question.line
    invoice = line.invoice
    neighbours = (
        db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice.id,
                                     InvoiceLine.line_no.between(line.line_no - 1, line.line_no + 1))
        .order_by(InvoiceLine.line_no).all()
    )
    crop = [
        schemas.CropLine(text=f"{n.description:<24.24} {n.qty:>5g}  {(n.pack_text or ''):<5.5} {n.unit_price:>8.2f} {n.extended:>9.2f}",
                         hit=n.id == line.id)
        for n in neighbours
    ]
    return schemas.QuestionOut(
        id=question.id, kind=question.kind, text=question.text,
        options=[schemas.QuestionOption(**o) for o in question.options], crop=crop,
        has_image=invoice.source in ("photo", "pdf"), supplier=_supplier_ref(db, line.supplier_id),
        invoice_id=invoice.id, invoice_date=invoice.invoice_date, line_id=line.id, dollar_impact=question.dollar_impact,
        status=question.status, answer=question.answer_key, created_at=question.created_at,
    )


def _open_questions(db: Session, restaurant_id: str | None) -> list[AgentQuestion]:
    return (
        db.query(AgentQuestion)
        .filter(AgentQuestion.restaurant_id == restaurant_id, AgentQuestion.status == OPEN)
        .order_by(AgentQuestion.dollar_impact.desc(), AgentQuestion.created_at)
        .all()
    )


# --- status and digest -----------------------------------------------------------------


@router.get("/status", response_model=schemas.StatusOut)
def get_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    scope = _scope(db, user)
    invoices = db.query(Invoice).filter(Invoice.restaurant_id == scope).order_by(Invoice.created_at).all()
    supplier_ids = list(dict.fromkeys(i.supplier_id for i in invoices))
    suppliers = [_supplier_ref(db, sid) for sid in supplier_ids]
    questions = len(_open_questions(db, scope))
    if invoices:
        first = min(i.invoice_date for i in invoices)
        first_digest = engine.week_start(first) + timedelta(days=7)
        count = len(invoices)
        names = [s.name for s in suppliers]
        who = names[0] if len(names) == 1 else f"{len(names)} suppliers"
        when = "First digest Monday." if first_digest > date.today() else "Digest is ready."
        progress = f"{count} invoice{'s' if count != 1 else ''} from {who}. {when}"
    else:
        first_digest = None
        progress = "No invoices yet. First digest the Monday after your first one."
    return schemas.StatusOut(invoice_count=len(invoices), suppliers=suppliers, first_digest=first_digest,
                             questions_open=questions, progress=progress, forward_address=settings.invoice_forward_address or None,
                             reader=extract.available(), billcom_configured=settings.billcom_configured,
                             digest_email_to=user.email)


@router.get("/digest", response_model=schemas.DigestOut)
def get_digest(user: User = Depends(current_user), db: Session = Depends(get_db)):
    scope = _scope(db, user)
    digest = engine.latest_digest(db, scope)
    db.commit()
    return engine.digest_out(db, digest)


@router.post("/digest/rebuild", response_model=schemas.DigestOut)
def rebuild_digest(user: User = Depends(manager), db: Session = Depends(get_db)):
    digest = engine.build_digest(db, _scope(db, user))
    db.commit()
    return engine.digest_out(db, digest)


@router.get("/digest/talking-points", response_model=schemas.TalkingPointsOut)
def get_talking_points(user: User = Depends(current_user), db: Session = Depends(get_db)):
    digest = engine.latest_digest(db, _scope(db, user))
    db.commit()
    return engine.talking_points(db, digest)


# --- items ----------------------------------------------------------------------------


def _item_for(db: Session, user: User, item_id: str) -> PriceItem:
    item = db.get(PriceItem, item_id)
    if item is None or item.restaurant_id != _scope(db, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item.")
    return item


@router.get("/items", response_model=list[schemas.ItemRow])
def list_items(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return engine.item_rows(db, _scope(db, user))


@router.get("/items/{item_id}", response_model=schemas.ItemDetail)
def get_item(item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    detail = engine.item_detail(db, _item_for(db, user, item_id))
    db.commit()
    return detail


@router.patch("/items/{item_id}", response_model=schemas.ItemDetail)
def patch_item(item_id: str, body: schemas.ItemPatch, user: User = Depends(manager), db: Session = Depends(get_db)):
    """"Wrong?": the pack size the owner says it is, applied to every line of the item. Or what the
    item is counted as in Inventory."""
    item = _item_for(db, user, item_id)
    if body.pack_size is not None:
        item.pack_size = body.pack_size
        if body.pack_unit:
            item.pack_unit = body.pack_unit.strip()[:32]
        for line in db.query(InvoiceLine).filter(InvoiceLine.item_id == item.id):
            line.pack_size = body.pack_size
        engine.build_digest(db, item.restaurant_id)
    if "inventory_item_id" in body.model_fields_set:
        if body.inventory_item_id:
            from app.restaurant.inventory import item_in

            if item_in(db, item.restaurant_id, body.inventory_item_id) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such inventory item.")
        stock.set_link(db, item, body.inventory_item_id or None)
    stock.sync(db, item.restaurant_id, trigger=f"{item.name} changed")
    db.commit()
    return engine.item_detail(db, item)


@router.get("/stock", response_model=list[schemas.StockRow])
def stock_rows(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """For the Inventory page: the last price the invoices show for each stock item, and reorders waiting."""
    return stock.rows(db, _scope(db, user))


def _row(db: Session, item: PriceItem) -> schemas.ItemRow:
    return next(r for r in engine.item_rows(db, item.restaurant_id) if r.id == item.id)


@router.post("/items/{item_id}/hide", response_model=schemas.ItemRow)
def hide_item(item_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    """"Not useful": out of the digest for four weeks. The only feedback there is."""
    item = _item_for(db, user, item_id)
    item.hidden_until = date.today() + timedelta(weeks=engine.HIDE_WEEKS)
    engine.build_digest(db, item.restaurant_id)
    db.commit()
    return _row(db, item)


@router.post("/items/{item_id}/unhide", response_model=schemas.ItemRow)
def unhide_item(item_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    item = _item_for(db, user, item_id)
    item.hidden_until = None
    engine.build_digest(db, item.restaurant_id)
    db.commit()
    return _row(db, item)


@router.post("/items/{item_id}/move", response_model=schemas.ItemRow)
def move_item(item_id: str, body: schemas.MoveIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Record that the owner is moving the item. Nothing is ordered or changed with any supplier."""
    item = _item_for(db, user, item_id)
    supplier = db.get(Supplier, body.supplier_id)
    if supplier is None or supplier.restaurant_id != item.restaurant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such supplier.")
    item.moved_to_supplier_id = supplier.id
    item.moved_at = datetime.now(timezone.utc)
    db.add(ThreadMessage(
        restaurant_id=item.restaurant_id, role="agent",
        text=f"{item.name} is moving to {supplier.name}. The next digest checks the invoice.",
        sources=[{"kind": "item", "id": item.id, "label": item.name}],
    ))
    engine.build_digest(db, item.restaurant_id)
    db.commit()
    return _row(db, item)


# --- questions ------------------------------------------------------------------------


@router.get("/questions", response_model=list[schemas.QuestionOut])
def list_questions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [question_out(db, q) for q in _open_questions(db, _scope(db, user))]


def _answer(db: Session, question: AgentQuestion, body: schemas.AnswerIn, by: str) -> schemas.QuestionOut:
    if body.answer == "other" and not (body.text or body.pack_size):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say what it is.")
    engine.answer_question(db, question, body.answer, body.text, body.pack_size, by)
    engine.build_digest(db, question.restaurant_id)
    db.commit()
    return question_out(db, question)


@router.post("/questions/{question_id}/answer", response_model=schemas.QuestionOut)
def answer_question(question_id: str, body: schemas.AnswerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    question = db.get(AgentQuestion, question_id)
    if question is None or question.restaurant_id != _scope(db, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such question.")
    if question.status != OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "That question is already answered.")
    return _answer(db, question, body, user.email)


# --- the thread -----------------------------------------------------------------------


@router.get("/thread", response_model=list[schemas.MessageOut])
def get_thread(limit: int = 200, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scope = _scope(db, user)
    engine.settle_pending(db, scope)
    db.commit()
    rows = (
        db.query(ThreadMessage).filter(ThreadMessage.restaurant_id == scope)
        .order_by(ThreadMessage.created_at.desc(), ThreadMessage.id.desc()).limit(min(max(limit, 1), 500)).all()
    )
    return [engine.message_out(db, m) for m in reversed(rows)]


@router.post("/thread", response_model=list[schemas.MessageOut], status_code=201)
def ask(body: schemas.AskIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A question from the ask field. Meeting prep is answered from the digest at once;
    anything else goes to the agent, and its answer arrives in the thread."""
    scope = _scope(db, user)
    text = body.text.strip()
    item = db.get(PriceItem, body.item_id) if body.item_id else None
    if item is not None and item.restaurant_id != scope:
        item = None
    shown = f"{item.name}: {text}" if item else text
    yours = ThreadMessage(restaurant_id=scope, role="you", text=shown, sources=[])
    db.add(yours)
    db.flush()
    new = [yours]

    if engine.is_meeting_prep(text):
        points = engine.talking_points(db, engine.latest_digest(db, scope))
        reply = ThreadMessage(restaurant_id=scope, role="agent", text="\n".join(f"{i}. {line}" for i, line in enumerate(points.lines, 1)),
                              sources=[s.model_dump() for s in points.sources])
        db.add(reply)
        new.append(reply)
    else:
        defn = agent_crud.get_definition_by_key(db, AGENT_KEY)
        restaurant = profile.restaurant_for(db, user)
        enabled = defn is not None and (restaurant is None or profile.agent_settings(restaurant, defn)[1])
        if not enabled:
            reply = ThreadMessage(restaurant_id=scope, role="agent", status="failed",
                                  text="The agent is switched off. Turn it on under Profile to ask questions.", sources=[])
            db.add(reply)
            new.append(reply)
        else:
            task = f"{QUESTION_PREFIX}{text}" + (f" (about {item.name}, item id {item.id})" if item else "")
            run = agent_crud.create_run(db, defn.id, trigger_type="manual", input=task, restaurant_id=scope)
            reply = ThreadMessage(restaurant_id=scope, role="agent", status="pending", text="", sources=[], run_id=run.id)
            db.add(reply)
            new.append(reply)
            db.commit()
            executor.submit(run.id, db)
    db.commit()
    return [engine.message_out(db, m) for m in new]


# --- drafts ---------------------------------------------------------------------------


def _draft_for(db: Session, user: User, draft_id: str) -> OutreachDraft:
    draft = db.get(OutreachDraft, draft_id)
    if draft is None or draft.restaurant_id != _scope(db, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such draft.")
    return draft


@router.post("/drafts", response_model=schemas.DraftOut, status_code=201)
def create_draft(body: schemas.DraftIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    item = _item_for(db, user, body.item_id)
    supplier_id = body.supplier_id
    if supplier_id is None:
        latest = (
            db.query(InvoiceLine).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .filter(InvoiceLine.item_id == item.id).order_by(Invoice.invoice_date.desc()).first()
        )
        if latest is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No invoice lines for this item yet.")
        supplier_id = latest.supplier_id
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or supplier.restaurant_id != item.restaurant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such supplier.")
    draft = engine.draft_for(db, item, supplier, body.kind)
    db.add(ThreadMessage(restaurant_id=item.restaurant_id, role="agent", text=f"Draft to {supplier.name} about {item.name}.",
                         sources=[{"kind": "item", "id": item.id, "label": item.name}], draft_id=draft.id))
    db.commit()
    return engine.draft_out(db, draft)


@router.get("/drafts/{draft_id}", response_model=schemas.DraftOut)
def get_draft(draft_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return engine.draft_out(db, _draft_for(db, user, draft_id))


@router.patch("/drafts/{draft_id}", response_model=schemas.DraftOut)
def patch_draft(draft_id: str, body: schemas.DraftPatch, user: User = Depends(manager), db: Session = Depends(get_db)):
    draft = _draft_for(db, user, draft_id)
    if draft.status != DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "That draft is finished.")
    for field_name in ("to_name", "to_email", "subject", "body"):
        value = getattr(body, field_name)
        if value is not None:
            setattr(draft, field_name, value.strip() or None if field_name in ("to_name", "to_email") else value)
    db.commit()
    return engine.draft_out(db, draft)


@router.post("/drafts/{draft_id}/send", response_model=schemas.DraftOut)
def send_draft(draft_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Marks the draft sent and remembers the rep. The email itself leaves from the owner's
    own mail app, which the page opens: nothing here sends anything."""
    draft = _draft_for(db, user, draft_id)
    if draft.status != DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "That draft is finished.")
    if not draft.to_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", draft.to_email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add the rep's email address first.")
    draft.status = SENT
    draft.sent_at = datetime.now(timezone.utc)
    draft.sent_by = user.email
    engine.remember_rep(db, draft)
    db.commit()
    return engine.draft_out(db, draft)


@router.post("/drafts/{draft_id}/discard", response_model=schemas.DraftOut)
def discard_draft(draft_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    draft = _draft_for(db, user, draft_id)
    if draft.status == DRAFT:
        draft.status = DISCARDED
        db.commit()
    return engine.draft_out(db, draft)


# --- invoices -------------------------------------------------------------------------

@router.post("/invoices/parse", response_model=schemas.ParseOut)
def parse_invoice(body: schemas.ParseIn, user: User = Depends(manager)):
    """Pasted or forwarded invoice text as lines: "DESCRIPTION  QTY  PACK  PRICE  TOTAL". Nothing is saved."""
    return extract.parse_text(body.text)


@router.post("/invoices/read", response_model=schemas.ReadOut)
def read_invoice(body: schemas.ReadIn, user: User = Depends(manager)):
    """A photo or PDF read into lines, each with how sure the reader was, and what doesn't add up. Nothing is saved."""
    try:
        return extract.read(body.media_type, extract.decode(body.data_base64))
    except extract.ReaderError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))


@router.post("/invoices", response_model=schemas.InvoiceSaved, status_code=201)
def add_invoice(body: schemas.InvoiceIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    scope = _scope(db, user)
    if body.invoice_date > date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That invoice date hasn't happened yet.")
    supplier = None
    if body.supplier_id:
        supplier = db.get(Supplier, body.supplier_id)
        if supplier is None or supplier.restaurant_id != scope:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such supplier.")
    elif body.supplier_name and body.supplier_name.strip():
        name = body.supplier_name.strip()
        supplier = db.query(Supplier).filter(Supplier.restaurant_id == scope, Supplier.name.ilike(name)).first()
        if supplier is None:
            supplier = Supplier(restaurant_id=scope, name=name)
            db.add(supplier)
            db.flush()
    if supplier is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name the supplier.")
    first_ever = db.query(Invoice.id).filter(Invoice.restaurant_id == scope).first() is None
    invoice, questions = engine.ingest_invoice(db, scope, supplier, body.invoice_date, body.lines, body.source, body.reference,
                                               total=body.total)
    engine.build_digest(db, scope)
    db.commit()
    matched = sum(1 for line in invoice.lines if line.item_id is not None and line.pack_size is not None)
    asked = [q for q in questions if q.status == OPEN]
    return schemas.InvoiceSaved(
        id=invoice.id, line_count=len(invoice.lines), matched=matched, questions_asked=len(asked),
        first_question=question_out(db, asked[0]) if first_ever and asked else None,
    )


def _invoice_row(db: Session, i: Invoice) -> schemas.InvoiceRow:
    return schemas.InvoiceRow(
        id=i.id, supplier=_supplier_ref(db, i.supplier_id), invoice_date=i.invoice_date, reference=i.reference,
        source=i.source, line_count=len(i.lines), total=round(sum(l.extended for l in i.lines), 2), printed_total=i.total,
        unmatched=sum(1 for l in i.lines if l.item_id is None or l.pack_size is None),
        status=i.status, review_reasons=list(i.review_reasons or []),
    )


@router.get("/invoices", response_model=list[schemas.InvoiceRow])
def list_invoices(user: User = Depends(current_user), db: Session = Depends(get_db)):
    scope = _scope(db, user)
    rows = db.query(Invoice).filter(Invoice.restaurant_id == scope).order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc()).limit(200).all()
    return [_invoice_row(db, i) for i in rows]


@router.post("/invoices/{invoice_id}/confirm", response_model=schemas.InvoiceRow)
def confirm_invoice(invoice_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    """"Looks right": an invoice that failed a check now counts."""
    scope = _scope(db, user)
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.restaurant_id != scope:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice.")
    invoice.status = engine.COMPLETE
    invoice.review_reasons = []
    engine.build_digest(db, scope)
    stock.sync(db, scope, trigger=f"invoice {invoice.reference or ''} confirmed".replace("  ", " "))
    db.commit()
    return _invoice_row(db, invoice)


# --- the digest as mail ---------------------------------------------------------------------


def _api_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.post("/digest/email", response_model=schemas.EmailOut)
def email_digest(request: Request, user: User = Depends(manager), db: Session = Depends(get_db)):
    """This week's digest, mailed to you now."""
    out = mail.send_digest(db, _scope(db, user), [user.email], settings.link_base or _api_base(request), _api_base(request))
    db.commit()
    return out


@router.get("/digest/email/preview", response_class=HTMLResponse)
def preview_digest_email(request: Request, user: User = Depends(manager), db: Session = Depends(get_db)):
    scope = _scope(db, user)
    digest = engine.latest_digest(db, scope)
    restaurant = db.get(Restaurant, scope) if scope else None
    _subject, body, _text = mail.render(db, digest, restaurant, settings.link_base or _api_base(request), _api_base(request))
    db.commit()
    return HTMLResponse(body)


@router.post("/digest/cron", response_model=schemas.CronOut)
def digest_cron(request: Request, db: Session = Depends(get_db)):
    """For an outside scheduler: mail every restaurant's digest that hasn't gone this week. Needs X-Cron-Secret."""
    if not settings.cron_secret or request.headers.get("x-cron-secret") != settings.cron_secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Set CRON_SECRET and send it as X-Cron-Secret.")
    sent = skipped = 0
    for (restaurant_id,) in db.query(Restaurant.id).filter(Restaurant.setup_completed_at.is_not(None)).all():
        try:
            if mail.send_weekly(db, restaurant_id):
                sent += 1
            else:
                skipped += 1
            db.commit()
        except Exception:  # one restaurant's mail failing mustn't stop the rest
            logger.exception("digest mail failed for restaurant %s", restaurant_id)
            db.rollback()
            skipped += 1
    return schemas.CronOut(sent=sent, skipped=skipped)


@router.get("/digest/not-useful/{token}", response_class=HTMLResponse)
def not_useful_link(token: str, db: Session = Depends(get_db)):
    """The "Not useful" link in the mail: the item is out of the digest for four weeks."""
    item = mail.apply_not_useful(db, token)
    if item is None:
        return HTMLResponse(_plain_page("That link has expired.", "Open the digest and press Not useful on the card instead."),
                            status_code=status.HTTP_410_GONE)
    db.commit()
    return HTMLResponse(_plain_page(f"{item.name} hidden.", f"It stays out of your digest for {engine.HIDE_WEEKS} weeks."))


def _plain_page(headline: str, line: str) -> str:
    link = f'<p><a href="{html.escape(settings.link_base)}/#digest" style="color:#111">Open the digest</a></p>' if settings.link_base else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<title>{html.escape(headline)}</title></head>'
        '<body style="margin:0;background:#fafafa;font-family:Inter,-apple-system,Helvetica,Arial,sans-serif;color:#111">'
        '<div style="max-width:560px;margin:0 auto;padding:48px 16px">'
        f'<h1 style="font-size:32px;line-height:40px;font-weight:600;margin:0">{html.escape(headline)}</h1>'
        f'<p style="font-size:17px;line-height:24px">{html.escape(line)}</p>{link}</div></body></html>'
    )


# --- Bill.com -------------------------------------------------------------------------------


@router.get("/billcom/status", response_model=schemas.BillcomStatusOut)
def billcom_status(user: User = Depends(manager), db: Session = Depends(get_db)):
    configured = settings.billcom_configured
    environment = None
    if configured:
        environment = settings.billcom_environment if settings.billcom_environment in billcom.GATEWAYS else "sandbox"
    return schemas.BillcomStatusOut(
        configured=configured, environment=environment,
        last_synced_at=billcom.last_synced_at(db, _scope(db, user)), webhook_path="/api/v1/prices/billcom/webhook",
    )


@router.post("/billcom/sync", response_model=schemas.BillcomSyncOut)
def billcom_sync(user: User = Depends(manager), db: Session = Depends(get_db)):
    """Pull bills from Bill.com now. Bills already in are left alone."""
    try:
        out = billcom.sync(db, _scope(db, user))
    except billcom.BillcomError as err:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(err))
    db.commit()
    return out


@router.post("/billcom/webhook")
async def billcom_webhook(request: Request, db: Session = Depends(get_db)):
    """Bill.com says a bill was created or updated. Needs X-Billcom-Secret.

    The payload is written raw to the webhook inbox, once per delivery, and a sync job is
    queued for the site's restaurant (one Bill.com organisation per site: the first restaurant
    set up). The handler itself writes no invoice, so the same webhook delivered twice, or a
    flood of them, yields one pull.
    """
    import hashlib
    import json

    from sqlalchemy.exc import IntegrityError

    from app.platform import jobs
    from app.platform.models import WebhookInbox

    if not settings.billcom_webhook_secret or request.headers.get("x-billcom-secret") != settings.billcom_webhook_secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Set BILLCOM_WEBHOOK_SECRET and send it as X-Billcom-Secret.")
    if not settings.billcom_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bill.com isn't connected on this site.")
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {"raw": raw.decode("utf-8", "replace")[:4000]}
    if not isinstance(payload, dict):
        payload = {"body": payload}
    external_id = str(payload.get("eventId") or payload.get("id") or hashlib.sha256(raw).hexdigest())[:128]
    restaurant = db.query(Restaurant).filter(Restaurant.setup_completed_at.is_not(None)).order_by(Restaurant.created_at).first()
    if restaurant is None:
        return {"ok": True, "queued": False}
    inbox = WebhookInbox(provider="billcom", external_id=external_id, restaurant_id=restaurant.id, payload=payload)
    db.add(inbox)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "queued": False, "duplicate": True}
    job = jobs.enqueue(db, "sync", f"sync:billcom:{restaurant.id}", {"provider": "billcom", "restaurant_id": restaurant.id},
                       restaurant_id=restaurant.id)
    inbox.job_id = job.id
    db.commit()
    return {"ok": True, "queued": True, "job_id": job.id}


# --- bookkeepers ------------------------------------------------------------------------


def bookkeeper(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if user.role != BOOKKEEPER:
        from app.auth.events import deny

        raise deny(db, user, request, "Only a bookkeeper can see the clients list.")
    return user


def _client_for(db: Session, user: User, restaurant_id: str) -> Restaurant:
    link = db.query(BookkeeperClient).filter(BookkeeperClient.user_id == user.id, BookkeeperClient.restaurant_id == restaurant_id).first()
    restaurant = db.get(Restaurant, restaurant_id) if link else None
    if restaurant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such client.")
    return restaurant


@router.get("/bookkeeper/clients", response_model=list[schemas.ClientRow])
def list_clients(user: User = Depends(bookkeeper), db: Session = Depends(get_db)):
    """Every client, the ones with questions waiting first."""
    month_start = date.today().replace(day=1)
    rows = []
    for link in db.query(BookkeeperClient).filter(BookkeeperClient.user_id == user.id):
        restaurant = db.get(Restaurant, link.restaurant_id)
        if restaurant is None:
            continue
        digests = db.query(PriceDigest).filter(PriceDigest.restaurant_id == restaurant.id).order_by(PriceDigest.week_start.desc()).all()
        rows.append(schemas.ClientRow(
            restaurant_id=restaurant.id, name=restaurant.name or "Unnamed restaurant",
            questions_open=len(_open_questions(db, restaurant.id)),
            last_digest=digests[0].week_start if digests else None,
            flagged_month=round(sum(d.total for d in digests if d.week_start >= month_start), 2),
        ))
    rows.sort(key=lambda r: (-r.questions_open, r.name))
    return rows


@router.get("/bookkeeper/clients/{restaurant_id}/questions", response_model=list[schemas.QuestionOut])
def client_questions(restaurant_id: str, user: User = Depends(bookkeeper), db: Session = Depends(get_db)):
    restaurant = _client_for(db, user, restaurant_id)
    return [question_out(db, q) for q in _open_questions(db, restaurant.id)]


@router.post("/bookkeeper/clients/{restaurant_id}/questions/{question_id}/answer", response_model=schemas.QuestionOut)
def client_answer(restaurant_id: str, question_id: str, body: schemas.AnswerIn, user: User = Depends(bookkeeper),
                  db: Session = Depends(get_db)):
    """The bookkeeper answers for the client. The owner sees the question resolved, not who by."""
    restaurant = _client_for(db, user, restaurant_id)
    question = db.get(AgentQuestion, question_id)
    if question is None or question.restaurant_id != restaurant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such question.")
    if question.status != OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "That question is already answered.")
    return _answer(db, question, body, user.email)
