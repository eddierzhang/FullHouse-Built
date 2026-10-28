"""Customer service requests: what customers said, and what to do about it.

- POST /customer-requests                    write one down (anyone signed in)
- GET  /customer-requests                    the requests, each in its original words
- POST /customer-requests/{id}/resolve       mark one dealt with, with a note
- POST /customer-requests/{id}/reopen
- GET  /customer-requests/reviews            the Assistant Manager's summaries and action items
- POST /customer-requests/reviews            ask it for a new one
- PATCH /customer-requests/action-items/{id} tick an action item off, or back on

Staff write requests down; reading them, with customers' names and
contact details, is for owners and admins. There is no route that edits
or deletes a request: the original stays as it was taken.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.agents import crud as agent_crud
from app.agents import executor
from app.agents.definitions import AGENT_KEY
from app.agents.models import AgentRun
from app.agents.schemas import AgentRunOut
from app.auth.deps import current_user, manager
from app.auth.models import User
from app.customers.models import (
    CHANNELS,
    DONE,
    KINDS,
    OPEN,
    RESOLVED,
    CustomerActionItem,
    CustomerRequest,
    CustomerServiceReview,
)
from app.db.session import get_db
from app.restaurant import profile
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/customer-requests", tags=["customer requests"])

#: What the Assistant Manager is asked when an owner wants the requests reviewed.
REVIEW_TASK = (
    "Customer service review: read the open customer requests, save a summary of what customers are "
    "saying -- the themes, and anything urgent -- and add an action item for each thing the restaurant "
    "should do about them, naming the requests it comes from."
)

Channel = Literal[CHANNELS]  # type: ignore[valid-type]
Kind = Literal[KINDS]  # type: ignore[valid-type]


def _optional(value: str | None) -> str | None:
    return (value or "").strip() or None


class RequestIn(BaseModel):
    message: str = Field(max_length=10000)
    channel: Channel = "in_person"
    kind: Kind = "other"
    customer_name: str | None = Field(default=None, max_length=120)
    customer_contact: str | None = Field(default=None, max_length=254)
    #: When the customer got in touch; now, if not given.
    received_at: datetime | None = None

    _name = field_validator("customer_name")(_optional)
    _contact = field_validator("customer_contact")(_optional)

    @field_validator("message")
    @classmethod
    def _message(cls, value: str) -> str:
        # Trimmed at the ends only: what's between is the customer's, kept as written.
        value = value.strip()
        if not value:
            raise ValueError("Write down what the customer said.")
        return value

    @field_validator("received_at")
    @classmethod
    def _received(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if value > datetime.now(timezone.utc):
            raise ValueError("That time hasn't happened yet.")
        return value


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    received_at: datetime
    channel: str
    kind: str
    customer_name: str | None
    customer_contact: str | None
    message: str
    logged_by: str
    created_at: datetime
    status: str
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None


class RequestLogged(BaseModel):
    """What whoever wrote a request down gets back: that it was saved, not the list."""

    id: str
    received_at: datetime


class ResolveIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)

    _note = field_validator("note")(_optional)


class ActionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    details: str | None
    priority: str
    request_ids: list[str]
    status: str
    done_at: datetime | None
    done_by: str | None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None
    summary: str
    request_ids: list[str]
    created_at: datetime
    action_items: list[ActionItemOut]


class ActionItemIn(BaseModel):
    done: bool


def _request_for(db: Session, user: User, request_id: str) -> CustomerRequest:
    """A request of the user's own restaurant; another's reads as missing."""
    row = db.get(CustomerRequest, request_id)
    if row is None or row.restaurant_id != scope_for(db, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such customer request.")
    return row


@router.post("", response_model=RequestLogged, status_code=201)
def log_request(body: RequestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Write down what a customer said. Anyone on the team can: they're the ones who hear it."""
    row = CustomerRequest(
        restaurant_id=scope_for(db, user), logged_by=user.email, message=body.message, channel=body.channel,
        kind=body.kind, customer_name=body.customer_name, customer_contact=body.customer_contact,
        received_at=body.received_at or datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return RequestLogged(id=row.id, received_at=row.received_at)


@router.get("", response_model=list[RequestOut])
def list_requests(
    status: Literal["open", "resolved"] | None = None, user: User = Depends(manager), db: Session = Depends(get_db)
):
    """Every request, newest first, each exactly as it was taken down."""
    query = db.query(CustomerRequest).filter(CustomerRequest.restaurant_id == scope_for(db, user))
    if status:
        query = query.filter(CustomerRequest.status == status)
    return query.order_by(CustomerRequest.received_at.desc(), CustomerRequest.id).limit(500).all()


# --- the Assistant Manager's reviews ------------------------------------------------
# Declared before the /{request_id} routes, which would otherwise read "reviews" as an id.


@router.get("/reviews", response_model=list[ReviewOut])
def list_reviews(limit: int = 5, user: User = Depends(manager), db: Session = Depends(get_db)):
    """The latest summaries, newest first, each with its action items."""
    return (
        db.query(CustomerServiceReview)
        .filter(CustomerServiceReview.restaurant_id == scope_for(db, user))
        .order_by(CustomerServiceReview.created_at.desc())
        .limit(min(max(limit, 1), 50))
        .all()
    )


@router.post("/reviews", response_model=AgentRunOut, status_code=202)
def start_review(user: User = Depends(manager), db: Session = Depends(get_db)):
    """Ask the Assistant Manager to summarise the open requests. Returns the run, which
    works in the background; the review appears on GET /reviews when it finishes."""
    scope = scope_for(db, user)
    defn = agent_crud.get_definition_by_key(db, AGENT_KEY)
    if defn is None or not profile.agent_settings(profile.restaurant_for(db, user), defn)[1]:
        raise HTTPException(status.HTTP_409_CONFLICT, "The Assistant Manager is switched off. Turn it on to ask for a summary.")
    waiting = db.query(CustomerRequest.id).filter(
        CustomerRequest.restaurant_id == scope, CustomerRequest.status == OPEN
    )
    if waiting.first() is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "There are no open requests to summarise.")
    in_flight = db.query(AgentRun.id).filter(
        AgentRun.restaurant_id == scope, AgentRun.input == REVIEW_TASK, AgentRun.status.in_(("queued", "running"))
    )
    if in_flight.first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A summary is already being written.")

    run = agent_crud.create_run(db, defn.id, trigger_type="manual", input=REVIEW_TASK, restaurant_id=scope)
    executor.submit(run.id)
    return run


@router.patch("/action-items/{item_id}", response_model=ActionItemOut)
def set_action_item(item_id: str, body: ActionItemIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Tick an action item off once it's done, or put it back."""
    item = db.get(CustomerActionItem, item_id)
    if item is None or item.restaurant_id != scope_for(db, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such action item.")
    if body.done != (item.status == DONE):
        item.status = DONE if body.done else OPEN
        item.done_at = datetime.now(timezone.utc) if body.done else None
        item.done_by = user.email if body.done else None
        db.commit()
        db.refresh(item)
    return item


# --- one request ----------------------------------------------------------------------


@router.get("/{request_id}", response_model=RequestOut)
def get_request(request_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    return _request_for(db, user, request_id)


@router.post("/{request_id}/resolve", response_model=RequestOut)
def resolve_request(request_id: str, body: ResolveIn, user: User = Depends(manager), db: Session = Depends(get_db)):
    """Mark a request dealt with. The note sits beside the original; it doesn't replace it."""
    row = _request_for(db, user, request_id)
    row.status = RESOLVED
    row.resolved_at = datetime.now(timezone.utc)
    row.resolved_by = user.email
    row.resolution_note = body.note
    db.commit()
    db.refresh(row)
    return row


@router.post("/{request_id}/reopen", response_model=RequestOut)
def reopen_request(request_id: str, user: User = Depends(manager), db: Session = Depends(get_db)):
    row = _request_for(db, user, request_id)
    if row.status != OPEN:
        row.status = OPEN
        row.resolved_at = row.resolved_by = None  # the note stays, as what was tried
        db.commit()
        db.refresh(row)
    return row
