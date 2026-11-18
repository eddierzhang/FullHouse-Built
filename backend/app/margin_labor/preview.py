"""The sample restaurants that sit beside the demo: Corner Grill, a small restaurant with every Margin & Labor
module live and the same facts on every page, and (for local previews only) an empty restaurant to fill in.

Corner Grill is seeded wherever the demo is: `app.demo.reset_now` starts `seed_in_background` after the demo
restaurant is ready, so the site answers at once and the Corner Grill button appears on the sign-in page when its
data is complete. `scripts/seed_preview.py` seeds both restaurants for `python dev.py margin-labor`.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PASSWORD = "demo-kitchen"
SAMPLE = ("demo@example.com", "Corner Grill")
EMPTY = ("empty@example.com", "Your Restaurant")
SAMPLE_LABEL = "Corner Grill: menu costs, usage, labor and the draft schedule"

#: Held while Corner Grill is being written, so a demo reset never wipes the tables under it.
_seeding = threading.Lock()


def _restaurant(db: Session, email: str, name: str, ready: bool):
    from app.auth.models import User
    from app.auth.security import hash_password
    from app.demo import DEMO_PROFILE
    from app.platform import flags
    from app.restaurant.models import Restaurant

    restaurant = Restaurant(**{**DEMO_PROFILE, "name": name}, setup_completed_at=datetime.now(timezone.utc) if ready else None)
    db.add(restaurant)
    db.flush()
    db.add(User(email=email, name="Sample Owner", password_hash=hash_password(PASSWORD), restaurant_id=restaurant.id))
    flags.enable_all(db, restaurant.id)
    for flag in (*flags.ML_FLAGS, flags.V2_CONNECT, flags.V2_DOCUMENTS):
        flags.set_flag(db, restaurant.id, flag, True)
    flags.set_flag(db, restaurant.id, flags.V3_VOICE, False)
    flags.set_flag(db, restaurant.id, flags.ML_CHANNELS, False)  # Channels is out of the app for now: no findings, no proposals
    db.commit()
    return restaurant


def seed_empty(db: Session) -> None:
    from app.auth.models import User

    if db.query(User).filter(User.email == EMPTY[0]).first() is None:
        _restaurant(db, *EMPTY, ready=True)


def seed_sample(db: Session, solve_seconds: int = 12) -> str | None:
    """Corner Grill, whole. Committed in small steps so nobody else waits on the database for long; the restaurant
    is marked set up only at the end, which is what puts its button on the sign-in page. None if it is there already."""
    from app.agents.definitions import AGENT_DEF
    from app.agents.models import AgentDefinition
    from app.auth.models import User
    from app.margin_labor import demand, fixtures, labor, labor_read, menu_costs, schedule, usage
    from app.restaurant.models import Restaurant

    if db.query(User).filter(User.email == SAMPLE[0]).first():
        return None
    if db.query(AgentDefinition).filter(AgentDefinition.key == AGENT_DEF["key"]).first() is None:
        # Proposals are filed under the agent: it has to exist before anything is proposed.
        db.add(AgentDefinition(role="main", model="claude-opus-5", enabled=True, **AGENT_DEF))
        db.flush()
    rid = _restaurant(db, *SAMPLE, ready=False).id
    service = labor.Service(db, rid)
    made = fixtures.seed_preview(db, rid, service.today, service.conf.timezone, datetime.now(timezone.utc).astimezone(service.tz), checkpoint=db.commit)
    db.commit()
    menu_costs.recost(db, rid)
    db.commit()
    fixtures.seed_books(db, rid, made, service.today, service.conf.timezone, checkpoint=db.commit)
    db.commit()
    usage.run(db, rid)
    demand.forecast_items(db, rid)
    db.commit()
    labor.forecast_labor(db, rid)
    db.commit()
    schedule.generate(db, rid, labor_read.default_week(service.today), seconds=solve_seconds)
    db.commit()
    db.get(Restaurant, rid).setup_completed_at = datetime.now(timezone.utc)
    db.commit()
    return rid


def ready(db: Session) -> bool:
    """Whether Corner Grill is there and complete."""
    from app.auth.models import User
    from app.restaurant.models import Restaurant

    user = db.query(User).filter(User.email == SAMPLE[0]).first()
    return bool(user and db.query(Restaurant.id).filter(Restaurant.id == user.restaurant_id, Restaurant.setup_completed_at.isnot(None)).first())


def wait() -> None:
    """Block until any seeding in flight has finished: called before the demo wipes its tables."""
    with _seeding:
        pass


def seed_in_background(solve_seconds: int = 6) -> threading.Thread:
    """Seed Corner Grill on its own thread and its own session. A failure is logged and leaves the demo as it was."""
    def work() -> None:
        from app.db.session import SessionLocal

        with _seeding:
            db = SessionLocal()
            try:
                started = datetime.now(timezone.utc)
                if seed_sample(db, solve_seconds) is not None:
                    logger.info("sample restaurant ready in %.1fs", (datetime.now(timezone.utc) - started).total_seconds())
            except Exception:  # noqa: BLE001 - the demo restaurant must stay up whatever happens here
                logger.exception("seeding the sample restaurant failed; the demo runs without it")
                db.rollback()
            finally:
                db.close()

    thread = threading.Thread(target=work, name="seed-sample", daemon=True)
    thread.start()
    return thread
