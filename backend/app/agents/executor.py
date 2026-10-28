"""Runs agents off the request thread, through the job queue.

`POST /agents/runs` used to call `execute_run` inline, then on a thread pool in the same
process. Runs are now `agent` jobs (app.platform.jobs): the web process enqueues and returns
the queued run; a worker -- threads in this process by default, or `python -m app.worker` --
claims the job and executes it. Progress still arrives over SSE through the broker, and at
most two runs per restaurant are in flight at once.

Cancellation is cooperative and in-process: the provider checks `is_cancelled` between tool
rounds. A separate worker process does not see a cancel asked for on the web process.
"""

import logging
import threading

from sqlalchemy.orm import Session

from app.agents.broker import TERMINAL, broker

logger = logging.getLogger(__name__)

#: Kept for callers that still import it: the worker thread count lives in settings now.
MAX_CONCURRENT_RUNS = 4

_cancelled: set[str] = set()
_lock = threading.Lock()


class RunCancelled(Exception):
    """Raised inside a run when an operator asks it to stop."""


def request_cancel(run_id: str) -> None:
    with _lock:
        _cancelled.add(run_id)


def is_cancelled(run_id: str) -> bool:
    with _lock:
        return run_id in _cancelled


def clear_cancel(run_id: str) -> None:
    with _lock:
        _cancelled.discard(run_id)


def submit(run_id: str, db: Session | None = None) -> None:
    """Queue a run for a worker and return immediately.

    With `db`, the job is written in that session (and committed with it) so the run and
    its job land together; without, a short session of its own is used.
    """
    from app.db.session import SessionLocal
    from app.platform import jobs

    clear_cancel(run_id)
    owns = db is None
    db = db or SessionLocal()
    try:
        from app.agents.models import AgentRun

        run = db.get(AgentRun, run_id)
        jobs.enqueue(db, "agent", f"agent:{run_id}", {"run_id": run_id},
                     restaurant_id=run.restaurant_id if run else None, max_attempts=1)
        db.commit()
    finally:
        if owns:
            db.close()


def execute_now(run_id: str, db: Session | None = None) -> None:
    """Run it here, on this thread: what the `agent` job handler calls, with the worker's session."""
    from app.agents.runner import execute_run

    try:
        execute_run(run_id, db=db)
    except Exception as exc:  # already recorded on the run by execute_run
        logger.warning("agent run %s ended with an error: %s", run_id, exc)
    finally:
        clear_cancel(run_id)
        # Tell listeners to stop waiting even when the run failed.
        broker.publish(run_id, {"type": TERMINAL, "run_id": run_id})


def shutdown(wait: bool = False) -> None:
    """Nothing to stop here any more: the worker threads are app.platform.jobs'."""
