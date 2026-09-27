import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents import executor, scheduler
from app.agents.broker import broker
from app.agents.routes import router as agents_router
from app.bootstrap import setup
from app.audit.middleware import AuditActorMiddleware
from app.audit.routes import router as changes_router
from app.config import settings
from app.restaurant.routes import router as restaurant_router

# Must happen before any session is used, so that no mutation escapes
# unrecorded during startup.
setup()

logger = logging.getLogger(__name__)


def _recover_orphaned_runs(db=None) -> int:
    """Close out runs left mid-flight by a previous process.

    Runs execute in this process's thread pool, so anything still marked
    queued or running at startup died with the last one. Left alone they
    sit in the UI forever claiming to be in progress.
    """
    from app.agents.models import AgentRun
    from app.audit.context import SYSTEM_ACTOR, actor_context
    from app.db.session import SessionLocal

    owns_session = db is None
    db = db or SessionLocal()
    try:
        orphans = db.query(AgentRun).filter(AgentRun.status.in_(("queued", "running"))).all()
        if not orphans:
            return 0
        with actor_context(SYSTEM_ACTOR):
            for run in orphans:
                run.status = "failed"
                run.error = "Interrupted: the server restarted while this run was in flight."
                run.finished_at = datetime.now(timezone.utc)
                db.add(run)
            db.commit()
        return len(orphans)
    finally:
        if owns_session:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Worker threads publish run events back onto this loop.
    broker.bind_loop(asyncio.get_running_loop())
    if settings.demo_mode:
        # A fresh demo restaurant on every start; nothing is left to recover.
        from app.demo import reset_now

        reset_now()
    else:
        recovered = _recover_orphaned_runs()
        if recovered:
            logger.warning("marked %d interrupted run(s) as failed at startup", recovered)
    if settings.scheduler_enabled:
        scheduler.start()
    yield
    scheduler.shutdown()
    executor.shutdown()


app = FastAPI(title="Restaurant Ops API", lifespan=lifespan)

app.add_middleware(AuditActorMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(restaurant_router)
app.include_router(agents_router)
app.include_router(changes_router)


@app.get("/health")
def health():
    return {"status": "ok"}


def mount_frontend(app: FastAPI, directory: str) -> None:
    """Serve the built frontend from the same process as the API.

    Used by the single-container image. Mounted after every API route, so
    those always take precedence over a static path.
    """
    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")


if settings.static_dir and Path(settings.static_dir).is_dir():
    mount_frontend(app, settings.static_dir)
