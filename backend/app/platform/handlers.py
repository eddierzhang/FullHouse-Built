"""What each queue does. Imported once at startup (app.main, app.worker) so the handlers register.

Every handler is idempotent: a job whose worker died is run again from the top.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.platform import events
from app.platform.jobs import handler
from app.platform.models import Event

logger = logging.getLogger(__name__)


@handler("agent")
def run_agent(db: Session, payload: dict) -> None:
    """Execute one queued agent run. A run already past queued is left alone: a worker that
    died mid-run leaves it `running`, and starting it over would double its proposals, so
    startup recovery marks those failed instead."""
    from app.agents import executor
    from app.agents.models import AgentRun

    run = db.get(AgentRun, payload["run_id"])
    if run is None or run.status != "queued":
        logger.info("agent job skipped: run %s is %s", payload.get("run_id"), run.status if run else "missing")
        return
    db.commit()
    executor.execute_now(run.id, db)


@handler("sync")
def sync_connector(db: Session, payload: dict) -> None:
    """Pull from one connected system for one restaurant, through its adapter."""
    from app.connectors import registry

    if payload.get("push_invoice_id"):
        registry.push_invoice(db, payload["restaurant_id"], payload["push_invoice_id"])
        return
    registry.run_sync(db, payload["restaurant_id"], payload["provider"], connection_id=payload.get("connection_id"))


@handler("digest")
def build_digest(db: Session, payload: dict) -> None:
    """Rebuild a restaurant's digest for the week that holds `today` (default: now)."""
    from app.prices import engine

    today = date.fromisoformat(payload["today"]) if payload.get("today") else None
    engine.build_digest(db, payload["restaurant_id"], today)


@handler("pnl")
def compute_pnl(db: Session, payload: dict) -> None:
    """The weekly P&L for one restaurant and week (default: the last complete week)."""
    from app.sales import pnl

    week_of = date.fromisoformat(payload["week_of"]) if payload.get("week_of") else pnl.last_complete_week()
    pnl.compute_week(db, payload["restaurant_id"], week_of)


@handler("ingest")
def ingest_document(db: Session, payload: dict) -> None:
    """A document that arrived by mail or upload: read it and make what it is."""
    from app.documents import registry

    registry.process(db, payload["document_id"], doc_type=payload.get("doc_type"))


@handler("extract")
def extract_document(db: Session, payload: dict) -> None:
    """The same as ingest; kept as its own queue so a flood of uploads doesn't hold up mail."""
    from app.documents import registry

    registry.process(db, payload["document_id"], doc_type=payload.get("doc_type"))


@handler("detect")
def weekly_detect(db: Session, payload: dict) -> None:
    """The weekly pass after the invoices: plate costs, menu margin, recipe onboarding, then the digest."""
    from app.platform import flags
    from app.prices import engine
    from app.recipes import engine as recipes

    restaurant_id = payload["restaurant_id"]
    today = date.fromisoformat(payload["today"]) if payload.get("today") else date.today()
    if flags.enabled(db, restaurant_id, flags.V2_DOCUMENTS):
        recipes.compute_plate_costs(db, restaurant_id, today=today)
        recipes.onboarding(db, restaurant_id, today)
    engine.build_digest(db, restaurant_id, today)


# --- events that cause jobs -------------------------------------------------------------------


@events.subscribe("invoice.ingested")
def _digest_after_invoice(db: Session, event: Event) -> None:
    from app.platform import jobs

    jobs.enqueue(db, "digest", f"digest:{event.restaurant_id}:{event.payload.get('week', '')}",
                 {"restaurant_id": event.restaurant_id}, restaurant_id=event.restaurant_id)
