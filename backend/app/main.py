import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents import executor, scheduler
from app.agents.broker import broker
from app.agents.routes import router as agents_router
from app.bootstrap import setup
from app.audit.middleware import AuditActorMiddleware
from app.audit.routes import router as changes_router
from app.auth.deps import current_user
from app.auth.routes import router as auth_router
from app.config import settings
from app.restaurant.routes import router as restaurant_router
from app.restaurant.profile_routes import router as profile_router
from app.support.alerts import configure_logging, configure_sentry
from app.support.routes import router as support_router

# Must happen before any session is used, so that no mutation escapes
# unrecorded during startup.
setup()
# Before the app exists, so Sentry (when configured) sees errors from the start.
configure_logging()
configure_sentry()

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
        if not settings.secret_key:
            logger.warning(
                "SECRET_KEY is not set: two-step sign-in secrets are encrypted with a development key. "
                "Set a long random SECRET_KEY before real people use this site, and never change it."
            )
    if settings.scheduler_enabled:
        scheduler.start()
    yield
    scheduler.shutdown()
    executor.shutdown()


app = FastAPI(title="Restaurant Ops API", lifespan=lifespan)

app.add_middleware(AuditActorMiddleware)

#: Sent on every response. The policy allows only this site's own scripts,
#: plus the Google Fonts stylesheet and font files the page loads; inline
#: styles (React's style props) and data: images (the two-step QR code).
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    # Browsers remember to use HTTPS only once they've reached the site over it.
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").startswith("https"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # So a frontend on another origin can name the export's download.
    expose_headers=["Content-Disposition"],
)

# Everything but signing in needs a signed-in user.
app.include_router(auth_router)
signed_in = [Depends(current_user)]
app.include_router(restaurant_router, dependencies=signed_in)
app.include_router(agents_router, dependencies=signed_in)
app.include_router(changes_router, dependencies=signed_in)
app.include_router(profile_router, dependencies=signed_in)
app.include_router(support_router, dependencies=signed_in)


@app.get("/health")
def health():
    """Up and able to reach the database -- what the host's health check and an
    uptime monitor need to know. A 503 when the database is unreachable, so
    the host marks the deploy unhealthy instead of serving errors."""
    from sqlalchemy import text

    from app.db.session import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check: database unreachable")
        return JSONResponse({"status": "error", "detail": "database unreachable"}, status_code=503)
    return {"status": "ok"}


def mount_frontend(app: FastAPI, directory: str) -> None:
    """Serve the built frontend from the same process as the API.

    Used by the single-container image. Mounted after every API route, so
    those always take precedence over a static path.
    """
    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")


if settings.static_dir and Path(settings.static_dir).is_dir():
    mount_frontend(app, settings.static_dir)
