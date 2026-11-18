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


@handler("forecast")
def run_forecast(db: Session, payload: dict) -> None:
    from app.forecast import engine as forecast
    from app.platform import flags

    if not flags.enabled(db, payload["restaurant_id"], flags.V2_ORDERS):
        return
    today = date.fromisoformat(payload["today"]) if payload.get("today") else None
    forecast.run(db, payload["restaurant_id"], today)


@handler("workflow")
def run_workflow(db: Session, payload: dict) -> None:
    """The scheduled workflows that make proposals: order drafts, prep sheets, recipe onboarding, the calendar scan."""
    from app.platform import flags

    restaurant_id = payload["restaurant_id"]
    today = date.fromisoformat(payload["today"]) if payload.get("today") else date.today()
    kind = payload.get("kind")
    if kind == "po_draft" and flags.enabled(db, restaurant_id, flags.V2_ORDERS):
        from app.restaurant import po_draft

        po_draft.run_all(db, restaurant_id, today)
    elif kind == "prep_sheet" and flags.enabled(db, restaurant_id, flags.V2_ORDERS):
        from app.forecast import prep

        prep.run_workflow(db, restaurant_id, today)
    elif kind == "recipe_onboarding" and flags.enabled(db, restaurant_id, flags.V2_DOCUMENTS):
        from app.recipes import engine as recipes

        recipes.onboarding(db, restaurant_id, today)
    elif kind == "autopilot" and flags.enabled(db, restaurant_id, flags.V3_AUTOPILOT):
        from app.ordering import autopilot

        autopilot.run(db, restaurant_id)
    elif kind == "late_orders" and flags.enabled(db, restaurant_id, flags.V3_RECEIVING):
        from app.receiving import engine as receiving

        receiving.late(db, restaurant_id, today)
    elif kind == "stock" and flags.enabled(db, restaurant_id, flags.V3_STOCK):
        from app.stock import engine as stock

        stock.run(db, restaurant_id, today)
    elif kind == "calendar_scan" and flags.enabled(db, restaurant_id, flags.V2_CALENDAR):
        try:
            from app.calendar import engine as calendar

            calendar.scan(db, restaurant_id, today)
        except ImportError:
            logger.info("calendar scan requested before the calendar phase exists")
    # Whatever the job just said that needs someone to act (an order about to go, a late delivery) is mailed now.
    from app.notify import service as notify

    notify.deliver_notices(db, restaurant_id)


@events.subscribe("sales_items.synced")
def _forecast_after_sales(db: Session, event: Event) -> None:
    from app.platform import flags, jobs

    if event.restaurant_id and flags.enabled(db, event.restaurant_id, flags.V2_ORDERS):
        jobs.enqueue(db, "forecast", f"forecast:{event.restaurant_id}:{date.today().isoformat()}",
                     {"restaurant_id": event.restaurant_id}, restaurant_id=event.restaurant_id)


@events.subscribe("sales_items.synced")
def _stock_after_sales(db: Session, event: Event) -> None:
    """New sales take their ingredients off the shelf as soon as they arrive, not at the next half hour."""
    from app.platform import flags, jobs

    if event.restaurant_id and flags.enabled(db, event.restaurant_id, flags.V3_STOCK):
        jobs.enqueue(db, "workflow", f"stock:{event.restaurant_id}:after-sync", {"restaurant_id": event.restaurant_id, "kind": "stock"},
                     restaurant_id=event.restaurant_id)


# --- events that cause jobs -------------------------------------------------------------------


@events.subscribe("invoice.ingested")
def _digest_after_invoice(db: Session, event: Event) -> None:
    from app.platform import jobs

    jobs.enqueue(db, "digest", f"digest:{event.restaurant_id}:{event.payload.get('week', '')}",
                 {"restaurant_id": event.restaurant_id}, restaurant_id=event.restaurant_id)


# --- Margin & Labor Engine: recomputes that run because an input changed ----------------------------


@handler("derive")
def derive(db: Session, payload: dict) -> None:
    """One recompute of a derived table, named by `job`. Each is a pure function of its inputs, so a retry or a
    replay leaves the same rows."""
    from app.platform import flags

    restaurant_id = payload["restaurant_id"]
    if not flags.any_margin_labor(db, restaurant_id):
        return
    if payload.get("job") == "rollup_intervals":
        from app.margin_labor import rollup

        rollup.rollup_intervals(db, restaurant_id, date.fromisoformat(payload["business_date"]))
    elif payload.get("job") == "recost" and flags.enabled(db, restaurant_id, flags.ML_MENU_COSTS):
        from app.margin_labor import menu_costs

        menu_costs.recost(db, restaurant_id, recipe_ids=set(payload["recipe_ids"]) if payload.get("recipe_ids") is not None else None,
                          item_ids=set(payload["item_ids"]) if payload.get("item_ids") is not None else None,
                          trigger_event_id=payload.get("trigger_event_id"))
    elif payload.get("job") in ("forecast_daily", "order_draft", "prep_sheet") and flags.enabled(db, restaurant_id, flags.ML_ORDERING):
        from app.margin_labor import demand, ordering

        if payload["job"] == "forecast_daily":
            demand.forecast_items(db, restaurant_id)
        elif payload["job"] == "order_draft":
            ordering.draft(db, restaurant_id, payload["supplier_id"])
        else:
            ordering.prep_sheet(db, restaurant_id, date.fromisoformat(payload["business_date"]))
    elif payload.get("job") == "schedule_generate" and flags.enabled(db, restaurant_id, flags.ML_LABOR):
        from app.margin_labor import schedule

        schedule.generate(db, restaurant_id, date.fromisoformat(payload["week_of"]))
    elif payload.get("job") == "forecast_labor" and flags.enabled(db, restaurant_id, flags.ML_LABOR):
        from app.margin_labor import labor

        labor.forecast_labor(db, restaurant_id)
    elif payload.get("job") == "channel_margins" and flags.enabled(db, restaurant_id, flags.ML_CHANNELS):
        from app.margin_labor import channels

        channels.run(db, restaurant_id)
    elif payload.get("job") == "usage_variance" and flags.enabled(db, restaurant_id, flags.ML_USAGE):
        from app.margin_labor import usage

        usage.run(db, restaurant_id)


@events.subscribe("sales.synced")
def _rollup_after_orders(db: Session, event: Event) -> None:
    """Order-level sales arrived: roll up each business date they touched. One job per restaurant-day."""
    from app.platform import jobs

    for day in (event.payload or {}).get("business_dates", [])[:800]:
        jobs.enqueue(db, "derive", f"derive:{event.restaurant_id}:rollup_intervals:{day}",
                     {"restaurant_id": event.restaurant_id, "job": "rollup_intervals", "business_date": day}, restaurant_id=event.restaurant_id)


def _queue_recost(db: Session, event: Event, field: str | None) -> None:
    """One recost per restaurant per set of inputs: the same event replayed queues nothing new while one waits."""
    import hashlib

    from app.platform import flags, jobs

    if not flags.enabled(db, event.restaurant_id, flags.ML_MENU_COSTS):
        return
    ids = sorted((event.payload or {}).get("ids", [])) if field else None
    key = hashlib.sha256(",".join(ids).encode()).hexdigest()[:16] if ids is not None else "all"
    payload = {"restaurant_id": event.restaurant_id, "job": "recost", "trigger_event_id": str(event.seq)}
    if field:
        payload[field] = ids
    jobs.enqueue(db, "derive", f"derive:{event.restaurant_id}:recost:{field or 'all'}:{key}", payload, restaurant_id=event.restaurant_id)


@events.subscribe("price_point.created")
def _recost_after_price(db: Session, event: Event) -> None:
    _queue_recost(db, event, "item_ids")


@events.subscribe("recipe.saved")
def _recost_after_recipe(db: Session, event: Event) -> None:
    _queue_recost(db, event, "recipe_ids")


@events.subscribe("sales_items.synced")
def _recost_after_sales(db: Session, event: Event) -> None:
    """New sales move what sold in 28 days and the fallback menu price: check every dish against the alert rule."""
    _queue_recost(db, event, None)


@events.subscribe("intervals.rolled_up")
def _recost_after_rollup(db: Session, event: Event) -> None:
    _queue_recost(db, event, None)


def _queue_usage(db: Session, event: Event) -> None:
    """Sales, an invoice price or a recipe changed: work the usage windows out again. One job per restaurant waits at a time."""
    from app.platform import flags, jobs

    if flags.enabled(db, event.restaurant_id, flags.ML_USAGE):
        jobs.enqueue(db, "derive", f"derive:{event.restaurant_id}:usage_variance", {"restaurant_id": event.restaurant_id, "job": "usage_variance"},
                     restaurant_id=event.restaurant_id)


for _type in ("price_point.created", "recipe.saved", "sales_items.synced", "intervals.rolled_up"):
    events.subscribe(_type)(_queue_usage)


def _queue_channels(db: Session, event: Event) -> None:
    """Orders, a payout report or a plate cost changed: work the channel margins out again."""
    from app.platform import flags, jobs

    if flags.enabled(db, event.restaurant_id, flags.ML_CHANNELS):
        jobs.enqueue(db, "derive", f"derive:{event.restaurant_id}:channel_margins", {"restaurant_id": event.restaurant_id, "job": "channel_margins"},
                     restaurant_id=event.restaurant_id)


for _type in ("payouts.synced", "plate_cost.changed", "intervals.rolled_up"):
    events.subscribe(_type)(_queue_channels)

from app.margin_labor import proposals as _ml_proposals  # noqa: E402

_ml_proposals.register()

from app.margin_labor import triggers as _ml_triggers  # noqa: E402

_ml_triggers.install()
