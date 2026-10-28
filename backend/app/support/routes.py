"""Getting help, telling us things, and taking your data with you.

- GET  /support              who to contact, for the Help page
- POST /support/feedback     the feedback button
- GET  /support/feedback     what the team has sent (owners and admins)
- POST /support/client-errors a crash in someone's browser, so it alerts like a server error
- GET  /support/export       everything the restaurant keeps here, as CSV files in a zip
                             (owners and admins; each export goes in the security log)
"""

import csv
import io
import json
import re
import threading
import time
import zipfile
from collections import defaultdict, deque
from datetime import date, datetime, time as time_of_day, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.config import settings
from app.db.session import get_db
from app.restaurant.inventory import scope_for
from app.support.alerts import _clean_url, report
from app.support.models import Feedback

router = APIRouter(prefix="/api/v1/support", tags=["support"])


class _Limiter:
    """At most `limit` calls per `window` seconds per key, in memory -- enough to
    stop one account filling the logs or the database, in this one process."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            calls = self._calls[key]
            while calls and calls[0] <= now - self.window:
                calls.popleft()
            if len(calls) >= self.limit:
                return False
            calls.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._calls.clear()


feedback_limit = _Limiter(limit=10, window=3600)
error_limit = _Limiter(limit=30, window=600)


def _page(value: str | None) -> str | None:
    """A route name, never a full link: invite and reset links carry live tokens."""
    if not value:
        return None
    return _clean_url(value.strip())[:100] or None


# --- contact -------------------------------------------------------------------------------


class SupportInfo(BaseModel):
    support_email: str | None
    support_url: str | None


@router.get("", response_model=SupportInfo)
def support_info(user: User = Depends(current_user)):
    return SupportInfo(support_email=settings.support_email, support_url=settings.support_url)


# --- feedback -----------------------------------------------------------------------------


class FeedbackIn(BaseModel):
    message: str = Field(max_length=2000)
    page: str | None = Field(default=None, max_length=255)

    @field_validator("message")
    @classmethod
    def _message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Write what happened or what you'd like.")
        return value


class FeedbackOut(BaseModel):
    id: int
    sent_by: str
    page: str | None
    message: str
    created_at: datetime


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
def send_feedback(payload: FeedbackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Anyone signed in can send feedback. Kept with their restaurant, and logged."""
    if not feedback_limit.allow(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "That's a lot of feedback at once. Try again in an hour.")
    item = Feedback(restaurant_id=scope_for(db, user), sent_by=user.email, page=_page(payload.page), message=payload.message)
    db.add(item)
    db.commit()
    db.refresh(item)
    # Logged, so it reaches whoever runs the site even without opening the app.
    import logging

    logging.getLogger("fullhouse.feedback").warning(
        "feedback from %s (restaurant %s, page %s): %s", user.email, item.restaurant_id, item.page, item.message[:500]
    )
    return FeedbackOut.model_validate(item, from_attributes=True)


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(user: User = Depends(manager), db: Session = Depends(get_db)):
    rows = (
        db.query(Feedback)
        .filter(Feedback.restaurant_id == scope_for(db, user))
        .order_by(Feedback.id.desc())
        .limit(200)
    )
    return [FeedbackOut.model_validate(row, from_attributes=True) for row in rows]


# --- browser errors -----------------------------------------------------------------------


class ClientErrorIn(BaseModel):
    message: str = Field(max_length=500)
    stack: str | None = Field(default=None, max_length=4000)
    page: str | None = Field(default=None, max_length=255)


@router.post("/client-errors", status_code=204)
def client_error(payload: ClientErrorIn, user: User = Depends(current_user)):
    """A crash in the browser, reported like a server error: logged, and alerted on with Sentry.
    Quietly dropped past the per-person limit, so a looping error can't flood anything."""
    if error_limit.allow(user.id):
        report(
            f"Browser error: {payload.message.strip()[:200]}",
            page=_page(payload.page),
            stack=_clean_url(payload.stack or "")[:4000],
            restaurant_id=user.restaurant_id,
            user_id=user.id,
        )
    return Response(status_code=204)


# --- export --------------------------------------------------------------------------------

#: Never exported, whatever table they turn up in: secrets, hashes and tokens.
_NEVER = {"password_hash", "mfa_secret", "mfa_pending_secret", "mfa_backup_codes", "token", "token_hash", "request_id"}

_FORMULA = re.compile(r"^[=+\-@\t\r]")


def _cell(value):
    """A spreadsheet-safe cell: text that a spreadsheet would run as a formula gets a leading '."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str)
    if isinstance(value, (datetime, date, time_of_day)):
        return value.isoformat()
    if isinstance(value, str) and _FORMULA.match(value):
        return "'" + value
    return value


def _csv(rows, columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(getattr(row, column, None)) for column in columns])
    return buffer.getvalue()


def _columns(model, drop=()) -> list[str]:
    return [c.name for c in model.__table__.columns if c.name not in _NEVER and c.name not in drop]


def export_files(db: Session, restaurant_id: str | None) -> dict[str, str]:
    """This restaurant's business data, one CSV per kind. Nothing from any other restaurant."""
    from app.agents.models import AgentAction, AgentRun
    from app.audit.crud import visible_to
    from app.audit.models import ChangeLog
    from app.restaurant.models import (
        InventoryAdjustment,
        InventoryItem,
        MenuItem,
        Order,
        OrderItem,
        RecipeItem,
        Restaurant,
        Shift,
        Staff,
        Supplier,
    )

    def own(model):
        return db.query(model).filter(model.restaurant_id == restaurant_id)

    files: dict[str, str] = {}

    def add(name: str, model, rows, drop=()):
        files[name] = _csv(rows, _columns(model, drop))

    add("restaurant.csv", Restaurant, db.query(Restaurant).filter(Restaurant.id == restaurant_id), drop=("completed_steps",))
    add("inventory_items.csv", InventoryItem, own(InventoryItem).order_by(InventoryItem.name))
    add("stock_history.csv", InventoryAdjustment, own(InventoryAdjustment).order_by(InventoryAdjustment.id))
    add("suppliers.csv", Supplier, own(Supplier).order_by(Supplier.name))
    menu = own(MenuItem).order_by(MenuItem.name).all()
    add("menu_items.csv", MenuItem, menu)
    add("recipes.csv", RecipeItem, db.query(RecipeItem).filter(RecipeItem.menu_item_id.in_([m.id for m in menu])))
    add("staff.csv", Staff, own(Staff).order_by(Staff.name))
    add("shifts.csv", Shift, own(Shift).order_by(Shift.date, Shift.start_time))
    orders = own(Order).order_by(Order.created_at).all()
    add("orders.csv", Order, orders)
    add("order_items.csv", OrderItem, db.query(OrderItem).filter(OrderItem.order_id.in_([o.id for o in orders])))
    add("agent_runs.csv", AgentRun, own(AgentRun).order_by(AgentRun.created_at))
    add("agent_recommendations.csv", AgentAction, own(AgentAction).order_by(AgentAction.created_at))
    add("change_history.csv", ChangeLog, db.query(ChangeLog).filter(visible_to(restaurant_id, True)).order_by(ChangeLog.id))
    add("feedback.csv", Feedback, own(Feedback).order_by(Feedback.id))
    from app.customers.models import CustomerActionItem, CustomerRequest, CustomerServiceReview

    add("customer_requests.csv", CustomerRequest, own(CustomerRequest).order_by(CustomerRequest.received_at))
    add("customer_service_summaries.csv", CustomerServiceReview, own(CustomerServiceReview).order_by(CustomerServiceReview.created_at))
    add("customer_action_items.csv", CustomerActionItem, own(CustomerActionItem).order_by(CustomerActionItem.created_at))
    from app.prices.models import AgentQuestion, Invoice, InvoiceLine, OutreachDraft, PriceDigest, PriceItem, ThreadMessage

    add("price_items.csv", PriceItem, own(PriceItem).order_by(PriceItem.name))
    add("supplier_invoices.csv", Invoice, own(Invoice).order_by(Invoice.invoice_date))
    add("supplier_invoice_lines.csv", InvoiceLine, own(InvoiceLine).order_by(InvoiceLine.invoice_id, InvoiceLine.line_no))
    add("agent_questions.csv", AgentQuestion, own(AgentQuestion).order_by(AgentQuestion.created_at), drop=("answered_by",))
    add("price_digests.csv", PriceDigest, own(PriceDigest).order_by(PriceDigest.week_start))
    add("supplier_messages.csv", OutreachDraft, own(OutreachDraft).order_by(OutreachDraft.created_at))
    add("agent_thread.csv", ThreadMessage, own(ThreadMessage).order_by(ThreadMessage.created_at))
    # The team: who, and in what role -- never passwords, two-step secrets or sessions.
    files["team.csv"] = _csv(
        db.query(User).filter(User.restaurant_id == restaurant_id).order_by(User.name),
        [c for c in ("name", "email", "role", "created_at", "disabled_at") if hasattr(User, c)],
    )
    return files


@router.get("/export")
def export(request: Request, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Download everything this restaurant keeps in FullHouse, as a zip of CSV files."""
    from app.auth import events

    restaurant_id = scope_for(db, user)
    files = export_files(db, restaurant_id)
    rows = {name: max(text.count("\n") - 1, 0) for name, text in files.items()}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            "Everything this restaurant keeps in FullHouse, exported "
            f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by {user.email}.\n"
            "One CSV file per kind of record; open them in any spreadsheet.\n"
            "Text that a spreadsheet would treat as a formula starts with ' so it can't run.\n",
        )
        for name, text in files.items():
            archive.writestr(name, text)

    events.record(db, "data_exported", request, actor=user, restaurant_id=restaurant_id,
                  detail={"files": len(files), "rows": sum(rows.values())})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="fullhouse-export-{stamp}.zip"', "Cache-Control": "no-store"},
    )
