"""A worker process: `python -m app.worker`. Same code as the web app, different entry.

Runs job-queue workers (app.platform.jobs) until stopped. The web app runs the same loop in a
thread by default (WORKER_INPROCESS=true), which is right for one free container; set
WORKER_INPROCESS=false on the web service and run this alongside it to scale the two apart.
"""

import logging
import signal
import threading

from app.bootstrap import setup
from app.config import settings
from app.platform import jobs
from app.support.alerts import configure_logging


def main() -> None:
    setup()
    configure_logging()
    import app.platform.handlers  # noqa: F401  registers the queue handlers

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    jobs.start_in_process(settings.worker_threads)
    logging.getLogger(__name__).info("worker process up with %d thread(s)", settings.worker_threads)
    stop.wait()
    jobs.stop_in_process()


if __name__ == "__main__":
    main()
