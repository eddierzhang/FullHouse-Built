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
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.audit.context import SYSTEM_ACTOR, actor_context, note_context
from app.config import settings

logger = logging.getLogger(__name__)

PROMOTION_SWEEP_JOB = "expire-promotions"
DEMO_RESET_JOB = "demo-reset"
_scheduler: BackgroundScheduler | None = None


class InvalidSchedule(ValueError):
    """A cron expression that cannot be parsed."""


def parse_cron(expression: str) -> CronTrigger:
    """Standard five-field crontab ("minute hour day month weekday"), in UTC."""
    try:
        return CronTrigger.from_crontab(expression.strip(), timezone=timezone.utc)
    except (ValueError, TypeError) as exc:
        raise InvalidSchedule(
            f"{expression!r} is not a valid cron schedule. Use five fields, "
            f"e.g. '0 9 * * *' for 09:00 UTC daily."
        ) from exc


def next_fire_time(expression: str | None, now: datetime | None = None) -> datetime | None:
    """When a schedule would next fire, whether or not the scheduler is running."""
    if not expression:
        return None
    now = now or datetime.now(timezone.utc)
    return parse_cron(expression).get_next_fire_time(None, now)


def _agent_job_id(key: str) -> str:
    return f"agent:{key}"


def run_scheduled_agent(agent_key: str, db=None) -> str | None:
    """Queue one scheduled run. Returns the run id, or None if skipped.

    Skips when the agent already has a run queued or in flight: a slow model
    on a frequent schedule would otherwise pile up runs faster than it
    finishes them.
    """
    from app.agents import crud, executor
    from app.agents.models import AgentRun
    from app.db.session import SessionLocal

    owns_session = db is None
    db = db or SessionLocal()
    try:
        defn = crud.get_definition_by_key(db, agent_key)
        if defn is None or not defn.enabled or not defn.schedule_cron:
            return None

        busy = (
            db.query(AgentRun)
            .filter(AgentRun.agent_definition_id == defn.id, AgentRun.status.in_(("queued", "running")))
            .count()
        )
        if busy:
            logger.info("skipping scheduled %s run: one is already in flight", agent_key)
            return None

        with actor_context(SYSTEM_ACTOR):
            run = crud.create_run(
                db, defn.id, trigger_type="scheduled", input=f"Scheduled run ({defn.schedule_cron} UTC)."
            )
        executor.submit(run.id)
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


def sync_agent_schedules() -> None:
    """Make the scheduler's agent jobs match the database."""
    if _scheduler is None:
        return

    from app.agents.models import AgentDefinition
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        wanted = {
            _agent_job_id(d.key): d
            for d in db.query(AgentDefinition).all()
            if d.enabled and d.schedule_cron
        }
    finally:
        db.close()

    for job in _scheduler.get_jobs():
        if job.id.startswith("agent:") and job.id not in wanted:
            job.remove()

    for job_id, defn in wanted.items():
        try:
            trigger = parse_cron(defn.schedule_cron)
        except InvalidSchedule:
            logger.warning("ignoring invalid schedule on %s: %r", defn.key, defn.schedule_cron)
            continue
        _scheduler.add_job(
            run_scheduled_agent,
            trigger,
            args=[defn.key],
            id=job_id,
            replace_existing=True,
            coalesce=True,       # after downtime, fire once rather than catching up
            max_instances=1,
            misfire_grace_time=300,
        )


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
