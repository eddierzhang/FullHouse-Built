"""Cron-scheduled agents, and promotions that end on their own.

Every AgentDefinition has had a `schedule_cron` column that nothing read.
This runs a background scheduler that turns each enabled schedule into
queued runs, and sweeps for promotions past their end time.

APScheduler's BackgroundScheduler rather than an asyncio one: the work it
starts -- creating runs, touching the database -- is synchronous, the same
reason runs use a thread pool. It lives in this process, which is fine for
the single-process deployment the in-process event broker already requires.
"""

import logging
from datetime import datetime, timezone, tzinfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.audit.context import SYSTEM, SYSTEM_ACTOR, Actor, actor_context, note_context
from app.config import settings

logger = logging.getLogger(__name__)

PROMOTION_SWEEP_JOB = "expire-promotions"
DEMO_RESET_JOB = "demo-reset"
STOCK_CHECK_JOB = "stock-check"
DIGEST_JOB = "digest-mail"

#: What the agent is asked at the owner's daily stock-check time.
STOCK_CHECK_TASK = (
    "Daily stock check: review stock levels against reorder points and supplier lead times, "
    "and propose reorders for anything running low."
)
_scheduler: BackgroundScheduler | None = None


class InvalidSchedule(ValueError):
    """A cron expression that cannot be parsed."""


def parse_cron(expression: str, tz: tzinfo = timezone.utc) -> CronTrigger:
    """Standard five-field crontab ("minute hour day month weekday"), on `tz`'s clock."""
    try:
        return CronTrigger.from_crontab(expression.strip(), timezone=tz)
    except (ValueError, TypeError) as exc:
        raise InvalidSchedule(
            f"{expression!r} is not a valid cron schedule. Use five fields, "
            f"e.g. '0 9 * * *' for 09:00 daily."
        ) from exc


def next_fire_time(expression: str | None, now: datetime | None = None, tz: tzinfo = timezone.utc) -> datetime | None:
    """When a schedule would next fire, whether or not the scheduler is running. Returned in UTC."""
    if not expression:
        return None
    now = now or datetime.now(timezone.utc)
    fire = parse_cron(expression, tz).get_next_fire_time(None, now)
    return fire.astimezone(timezone.utc) if fire else None


def agent_timezone(db) -> tzinfo:
    """Agent schedules run on the restaurant's clock once setup has set one."""
    from app.restaurant.profile import restaurant_timezone

    return restaurant_timezone(db)


def _job_id(prefix: str, restaurant_id: str | None) -> str:
    return f"{prefix}:{restaurant_id}" if restaurant_id else prefix


def _agent_job_id(key: str, restaurant_id: str | None = None) -> str:
    return _job_id(f"agent:{key}", restaurant_id)


def _system_for(restaurant_id: str | None) -> Actor:
    """The system, at work for one restaurant: its clock, its settings, its records."""
    return Actor(type=SYSTEM, id=SYSTEM_ACTOR.id, restaurant_id=restaurant_id)


def run_scheduled_agent(
    agent_key: str, db=None, task: str | None = None, restaurant_id: str | None = None
) -> str | None:
    """Queue one scheduled run for a restaurant. Returns the run id, or None if skipped.

    `task` is what the run is asked to do; by default, a note of the schedule
    that fired, which leaves the agent to its standing instructions. With no
    `restaurant_id` the run is for the site's restaurant.

    Skips when the restaurant already has a run of this agent queued or in
    flight: a slow model on a frequent schedule would otherwise pile up runs
    faster than it finishes them.
    """
    from app.agents import crud, executor
    from app.agents.models import AgentRun
    from app.db.session import SessionLocal
    from app.restaurant.profile import agent_settings, get_restaurant

    owns_session = db is None
    db = db or SessionLocal()
    try:
        with actor_context(_system_for(restaurant_id)):
            restaurant = get_restaurant(db)
            defn = crud.get_definition_by_key(db, agent_key)
            if defn is None:
                return None
            cron, enabled = agent_settings(restaurant, defn)
            if not enabled or not cron:
                return None

            in_flight = db.query(AgentRun).filter(
                AgentRun.agent_definition_id == defn.id, AgentRun.status.in_(("queued", "running"))
            )
            if restaurant is not None:
                in_flight = in_flight.filter(AgentRun.restaurant_id == restaurant.id)
            if in_flight.count():
                logger.info("skipping scheduled %s run: one is already in flight", agent_key)
                return None

            run = crud.create_run(
                db, defn.id, trigger_type="scheduled",
                input=task or f"Scheduled run ({cron}, {getattr(agent_timezone(db), 'key', 'UTC')}).",
            )
        executor.submit(run.id, db)
        return run.id
    finally:
        if owns_session:
            db.close()


def expire_promotions(db=None, now: datetime | None = None) -> int:
    """Restore regular prices on promotions that have ended.

    Recorded in the change log as the system ending the promotion, so the
    menu's history shows both the promo starting and stopping.
    """
    from app.db.session import SessionLocal
    from app.restaurant.models import MenuItem

    owns_session = db is None
    db = db or SessionLocal()
    now = now or datetime.now(timezone.utc)
    try:
        candidates = (
            db.query(MenuItem)
            .filter(MenuItem.promo_ends_at.is_not(None), MenuItem.regular_price.is_not(None))
            .all()
        )
        ended = []
        for item in candidates:
            ends_at = item.promo_ends_at
            if ends_at.tzinfo is None:  # SQLite returns naive UTC
                ends_at = ends_at.replace(tzinfo=timezone.utc)
            if ends_at <= now:
                ended.append(item)

        if not ended:
            return 0

        with actor_context(SYSTEM_ACTOR), note_context("Promotion ended"):
            for item in ended:
                item.price = item.regular_price
                item.regular_price = None
                item.promo_ends_at = None
                db.add(item)
            db.commit()
        logger.info("ended %d promotion(s)", len(ended))
        return len(ended)
    finally:
        if owns_session:
            db.close()


def _scheduled_restaurants(db) -> list[str | None]:
    """Whose schedules run: every restaurant that has finished setup. A site
    with no restaurant at all (sample data, before anyone signs up) runs the
    agent definitions' own schedules, for nobody in particular."""
    from app.restaurant.models import Restaurant

    if db.query(Restaurant.id).first() is None:
        return [None]
    ready = db.query(Restaurant.id).filter(Restaurant.setup_completed_at.is_not(None))
    return [row[0] for row in ready.order_by(Restaurant.created_at)]


def sync_agent_schedules() -> None:
    """Make the scheduler's jobs match the database: for each restaurant, the
    agent's schedule and the daily stock check, on that restaurant's clock."""
    if _scheduler is None:
        return

    from app.agents.models import AgentDefinition
    from app.db.session import SessionLocal
    from app.restaurant.models import Restaurant
    from app.restaurant.profile import agent_settings

    #: job id -> (what to call, cron, its clock, arguments)
    wanted: dict[str, tuple] = {}
    db = SessionLocal()
    try:
        definitions = db.query(AgentDefinition).all()
        for restaurant_id in _scheduled_restaurants(db):
            restaurant = db.get(Restaurant, restaurant_id) if restaurant_id else None
            with actor_context(_system_for(restaurant_id)):
                tz = agent_timezone(db)
                for defn in definitions:
                    cron, enabled = agent_settings(restaurant, defn)
                    if enabled and cron:
                        wanted[_agent_job_id(defn.key, restaurant_id)] = (
                            run_scheduled_agent, cron, tz, {"agent_key": defn.key, "restaurant_id": restaurant_id}
                        )
                # The owner's daily stock check: the same agent, asked a set question.
                if stock_check := stock_check_cron(db):
                    wanted[_job_id(STOCK_CHECK_JOB, restaurant_id)] = (
                        run_stock_check, stock_check, tz, {"restaurant_id": restaurant_id}
                    )
                # The weekly price digest, mailed Monday morning on the restaurant's clock.
                if restaurant_id is not None and settings.digest_cron:
                    wanted[_job_id(DIGEST_JOB, restaurant_id)] = (
                        run_digest_mail, settings.digest_cron, tz, {"restaurant_id": restaurant_id}
                    )
    finally:
        db.close()

    for job in _scheduler.get_jobs():
        if job.id.startswith(("agent:", STOCK_CHECK_JOB, DIGEST_JOB)) and job.id not in wanted:
            job.remove()

    for job_id, (func, cron, tz, kwargs) in wanted.items():
        try:
            trigger = parse_cron(cron, tz)
        except InvalidSchedule:
            logger.warning("ignoring invalid schedule on %s: %r", job_id, cron)
            continue
        _scheduler.add_job(
            func,
            trigger,
            kwargs=kwargs,
            id=job_id,
            replace_existing=True,
            coalesce=True,       # after downtime, fire once rather than catching up
            max_instances=1,
            misfire_grace_time=300,
        )


def run_stock_check(db=None, restaurant_id: str | None = None) -> str | None:
    from app.agents.definitions import AGENT_KEY

    return run_scheduled_agent(AGENT_KEY, db, task=STOCK_CHECK_TASK, restaurant_id=restaurant_id)


def stock_check_cron(db) -> str | None:
    """The daily stock check from Restaurant Settings, as cron; None when it's off."""
    from app.restaurant.profile import inventory_settings

    prefs = inventory_settings(db)
    if prefs is None or not prefs.daily_stock_check:
        return None
    hour, minute = prefs.daily_stock_check.split(":")
    return f"{int(minute)} {int(hour)} * * *"


def run_digest_mail(restaurant_id: str | None, db=None) -> bool:
    """Mail this week's price digest to a restaurant's owners and admins, once."""
    from app.db.session import SessionLocal
    from app.prices import mail

    owns_session = db is None
    db = db or SessionLocal()
    try:
        with actor_context(_system_for(restaurant_id)):
            sent = mail.send_weekly(db, restaurant_id)
        db.commit()
        return sent
    except Exception:
        db.rollback()
        logger.exception("digest mail failed for restaurant %s", restaurant_id)
        return False
    finally:
        if owns_session:
            db.close()


def _reset_demo() -> None:
    from app.demo import reset_now

    reset_now()
    sync_agent_schedules()  # the reset replaced the agent definitions


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone=timezone.utc)
    _scheduler.add_job(
        expire_promotions, "interval", minutes=1, id=PROMOTION_SWEEP_JOB,
        coalesce=True, max_instances=1,
    )
    from app.platform import workflows

    _scheduler.add_job(workflows.tick, "interval", minutes=1, id="workflows-tick", coalesce=True, max_instances=1)
    if settings.demo_mode:
        _scheduler.add_job(
            _reset_demo, parse_cron(settings.demo_reset_cron), id=DEMO_RESET_JOB,
            coalesce=True, max_instances=1,
        )
    _scheduler.start()
    sync_agent_schedules()


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def is_running() -> bool:
    return _scheduler is not None
