"""The job queue: enqueue from anywhere, run in a worker.

    jobs.enqueue(db, "sync", f"sync:billcom:{restaurant_id}", {"provider": "billcom", "restaurant_id": rid})

Queues: ingest, extract, normalize, detect, digest, sync, forecast, agent. A handler is a
function `(db, payload) -> None` registered per queue with `@handler("sync")`; it must be
idempotent, because a job whose worker died is leased again and run again.

Claiming is one conditional UPDATE (`WHERE status = 'queued'`), which is atomic on Postgres
and SQLite alike, so several worker threads or processes share one table safely. Leases that
expire (a worker killed mid-job) go back to queued. Failures retry with exponential backoff
up to `max_attempts`, then stay `failed` with the error for the ops page.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.platform.models import ACTIVE, DONE, FAILED, QUEUED, Job

logger = logging.getLogger(__name__)

QUEUES = ("ingest", "extract", "normalize", "detect", "digest", "sync", "forecast", "agent", "pnl")
#: Agent runs in flight per restaurant at once; beyond this they wait in the queue.
AGENT_RUNS_PER_RESTAURANT = 2
BACKOFF_BASE_SECONDS = 5

Handler = Callable[[Session, dict], None]
_handlers: dict[str, Handler] = {}


def handler(queue: str) -> Callable[[Handler], Handler]:
    if queue not in QUEUES:
        raise ValueError(f"Unknown queue {queue!r}")

    def register(fn: Handler) -> Handler:
        _handlers[queue] = fn
        return fn

    return register


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db: Session, queue: str, singleton_key: str, payload: dict | None = None, *,
            restaurant_id: str | None = None, run_after: datetime | None = None, max_attempts: int = 5) -> Job:
    """Queue a job, or return the one already queued or active under the same key. Flushes, doesn't commit."""
    if queue not in QUEUES:
        raise ValueError(f"Unknown queue {queue!r}")
    existing = (
        db.query(Job).filter(Job.singleton_key == singleton_key, Job.status.in_((QUEUED, ACTIVE))).first()
    )
    if existing is not None:
        return existing
    job = Job(restaurant_id=restaurant_id, queue=queue, singleton_key=singleton_key, payload=payload or {},
              run_after=run_after or _now(), max_attempts=max_attempts)
    db.add(job)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # Two requests enqueued the same key at once: the other one is the job.
        db.expunge(job)
        return db.query(Job).filter(Job.singleton_key == singleton_key, Job.status.in_((QUEUED, ACTIVE))).one()
    return job


def recover_leases(db: Session) -> int:
    """Jobs whose worker went away: back to queued so another picks them up."""
    freed = db.execute(
        update(Job).where(Job.status == ACTIVE, Job.leased_until < _now())
        .values(status=QUEUED, leased_until=None, worker=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    if freed:
        db.commit()
    return freed


def _agent_slots_full(db: Session, restaurant_id: str | None) -> bool:
    if restaurant_id is None:
        return False
    active = db.query(Job).filter(Job.queue == "agent", Job.status == ACTIVE, Job.restaurant_id == restaurant_id).count()
    return active >= AGENT_RUNS_PER_RESTAURANT


def claim(db: Session, worker: str, queues: tuple[str, ...] = QUEUES, lease_seconds: int | None = None) -> Job | None:
    """The next job this worker may run, marked active under a lease; None when there is nothing."""
    lease = timedelta(seconds=lease_seconds or settings.job_lease_seconds)
    now = _now()
    candidates = (
        db.query(Job).filter(Job.status == QUEUED, Job.queue.in_(queues), Job.run_after <= now)
        .order_by(Job.created_at).limit(20).all()
    )
    for job in candidates:
        if job.queue == "agent" and _agent_slots_full(db, job.restaurant_id):
            continue
        won = db.execute(
            update(Job).where(Job.id == job.id, Job.status == QUEUED)
            .values(status=ACTIVE, leased_until=now + lease, worker=worker, attempts=Job.attempts + 1, started_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount == 1
        if won:
            db.commit()
            db.refresh(job)
            return job
    db.rollback()
    return None


def run_job(db: Session, job: Job) -> bool:
    """Run one claimed job to done or to a retry. True when it finished."""
    from app.audit.context import SYSTEM_ACTOR, Actor, actor_context
    from app.support.alerts import log_context

    fn = _handlers.get(job.queue)
    started = time.monotonic()
    actor = Actor(type=SYSTEM_ACTOR.type, id=f"job:{job.queue}", restaurant_id=job.restaurant_id)
    try:
        if fn is None:
            raise RuntimeError(f"No handler registered for queue {job.queue!r}")
        with log_context(restaurant_id=job.restaurant_id, job_id=job.id), actor_context(actor):
            fn(db, dict(job.payload or {}))
            db.commit()
        db.execute(
            update(Job).where(Job.id == job.id).values(
                status=DONE, status_group=job.id, finished_at=_now(), leased_until=None, error=None,
                duration_ms=int((time.monotonic() - started) * 1000),
            ).execution_options(synchronize_session=False)
        )
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:2000]
        failed = job.attempts >= job.max_attempts
        delay = timedelta(seconds=BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1)))
        db.execute(
            update(Job).where(Job.id == job.id).values(
                status=FAILED if failed else QUEUED, status_group=job.id if failed else "open",
                run_after=_now() + delay, leased_until=None, worker=None, error=error,
                finished_at=_now() if failed else None, duration_ms=int((time.monotonic() - started) * 1000),
            ).execution_options(synchronize_session=False)
        )
        db.commit()
        logger.warning("job %s (%s) %s: %s", job.id, job.queue, "failed for good" if failed else "will retry", error)
        return False


def run_once(db: Session, worker: str = "test", queues: tuple[str, ...] = QUEUES) -> bool:
    """Claim and run one job. False when the queue is empty. For tests and the CLI."""
    recover_leases(db)
    job = claim(db, worker, queues)
    if job is None:
        return False
    run_job(db, job)
    return True


def drain(db: Session, worker: str = "test", queues: tuple[str, ...] = QUEUES, limit: int = 1000) -> int:
    """Run jobs until the queue is empty or `limit` is reached. Returns how many ran."""
    ran = 0
    while ran < limit and run_once(db, worker, queues):
        ran += 1
    return ran


# --- the worker loop -------------------------------------------------------------------


class Worker:
    """A loop that claims and runs jobs until stopped. One per thread; any number per process."""

    def __init__(self, name: str | None = None, queues: tuple[str, ...] = QUEUES, poll_seconds: float | None = None):
        self.name = name or f"{socket.gethostname()}:{threading.get_ident()}"
        self.queues = queues
        self.poll_seconds = poll_seconds or settings.worker_poll_seconds
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from app.db.session import SessionLocal

        logger.info("worker %s started on %s", self.name, ", ".join(self.queues))
        while not self._stop.is_set():
            db = SessionLocal()
            try:
                busy = run_once(db, self.name, self.queues)
            except Exception:
                logger.exception("worker %s: the loop itself failed; continuing", self.name)
                busy = False
            finally:
                db.close()
            if not busy:
                self._stop.wait(self.poll_seconds)
        logger.info("worker %s stopped", self.name)


_threads: list[tuple[Worker, threading.Thread]] = []


def start_in_process(threads: int | None = None) -> None:
    """Worker threads inside the web process: the default for a single free container."""
    count = threads if threads is not None else settings.worker_threads
    for i in range(count):
        worker = Worker(name=f"inproc-{i}")
        thread = threading.Thread(target=worker.run, name=f"job-worker-{i}", daemon=True)
        thread.start()
        _threads.append((worker, thread))


def stop_in_process(wait: float = 2.0) -> None:
    for worker, _ in _threads:
        worker.stop()
    for _, thread in _threads:
        thread.join(timeout=wait)
    _threads.clear()


def queue_stats(db: Session) -> list[dict]:
    """Per queue: how many queued, active, failed, and done in the last day. For the ops page."""
    from sqlalchemy import func

    since = _now() - timedelta(days=1)
    rows = (
        db.query(Job.queue, Job.status, func.count(Job.id), func.avg(Job.duration_ms))
        .filter((Job.status != DONE) | (Job.finished_at >= since))
        .group_by(Job.queue, Job.status).all()
    )
    out: dict[str, dict] = {q: {"queue": q, "queued": 0, "active": 0, "failed": 0, "done_24h": 0, "avg_ms": None} for q in QUEUES}
    for queue, status, count, avg_ms in rows:
        bucket = out.setdefault(queue, {"queue": queue, "queued": 0, "active": 0, "failed": 0, "done_24h": 0, "avg_ms": None})
        key = "done_24h" if status == DONE else status
        bucket[key] = count
        if status == DONE:
            bucket["avg_ms"] = int(avg_ms) if avg_ms is not None else None
    return list(out.values())
