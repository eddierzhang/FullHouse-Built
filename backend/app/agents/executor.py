"""Runs agents off the request thread.

`POST /agents/runs` used to call `execute_run` inline, so the HTTP
request stayed open for the whole run -- minutes, once a Boss starts
delegating. Runs now go to a small thread pool and the caller gets the
queued run straight back; progress arrives over SSE.

A thread pool rather than asyncio tasks because everything the run
touches is synchronous: the SQLAlchemy session, the Anthropic client and
the httpx calls to Ollama.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from app.agents.broker import TERMINAL, broker

logger = logging.getLogger(__name__)

#: Small on purpose. Each worker holds a database session and talks to a
#: model backend; unbounded concurrency would exhaust both.
MAX_CONCURRENT_RUNS = 4

_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RUNS, thread_name_prefix="agent-run")
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


def _live_pool() -> ThreadPoolExecutor:
    """The worker pool, recreated if a previous app lifespan shut it down."""
    global _pool
    if getattr(_pool, "_shutdown", False):
        _pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RUNS, thread_name_prefix="agent-run")
    return _pool


def submit(run_id: str) -> None:
    """Queue a run for execution and return immediately."""
    clear_cancel(run_id)
    _live_pool().submit(_execute, run_id)


def _execute(run_id: str) -> None:
    from app.agents.runner import execute_run

    try:
        execute_run(run_id)
    except Exception as exc:  # already recorded on the run by execute_run
        logger.warning("agent run %s ended with an error: %s", run_id, exc)
    finally:
        clear_cancel(run_id)
        # Tell listeners to stop waiting even when the run failed.
        broker.publish(run_id, {"type": TERMINAL, "run_id": run_id})


def shutdown(wait: bool = False) -> None:
    _pool.shutdown(wait=wait, cancel_futures=True)
