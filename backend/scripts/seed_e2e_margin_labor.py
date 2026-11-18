"""A second restaurant for the Margin & Labor browser tests, with its own owner, so nothing it holds shows up in
the other suites. Layered on seed_e2e.py; skipped on the fresh site, which must stay empty."""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.models import User  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.demo import DEMO_PROFILE  # noqa: E402
from app.margin_labor import channels, demand, fixtures, menu_costs, usage  # noqa: E402
from app.margin_labor import settings as ml_settings  # noqa: E402
from app.platform import flags  # noqa: E402
from app.restaurant.models import Restaurant  # noqa: E402

EMAIL = "ml@example.com"
PASSWORD = "ml-password"
NAME = "Margin Test Kitchen"


def seed() -> None:
    if os.environ.get("E2E_FRESH_SITE") == "1":
        return
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == EMAIL).first():
            print("Margin & Labor test kitchen already present, skipping")
            return
        profile = {**DEMO_PROFILE, "name": NAME}
        restaurant = Restaurant(**profile, setup_completed_at=datetime.now(timezone.utc))
        db.add(restaurant)
        db.flush()
        db.add(User(email=EMAIL, name="Margin Owner", password_hash=hash_password(PASSWORD), restaurant_id=restaurant.id))
        db.add(User(email="ml-staff@example.com", name="Margin Staff", password_hash=hash_password(PASSWORD), restaurant_id=restaurant.id, role="member"))
        for flag in (flags.V2_CONNECT, flags.V2_DOCUMENTS, flags.ML_MENU_COSTS, flags.ML_USAGE, flags.ML_CHANNELS, flags.ML_ORDERING):
            flags.set_flag(db, restaurant.id, flag, True)
        db.commit()
        today = menu_costs.load(db, restaurant.id).today
        made = fixtures.seed_kitchen(db, restaurant.id, today)
        fixtures.seed_schedule(db, restaurant.id, made["supplier"].id, minimum="150")
        fixtures.seed_orders(db, restaurant.id, today, ml_settings.for_restaurant(db, restaurant.id).timezone)
        db.commit()
        menu_costs.recost(db, restaurant.id)
        db.commit()
        usage.run(db, restaurant.id)
        channels.run(db, restaurant.id)
        demand.forecast_items(db, restaurant.id)
        db.commit()
        print("Seeded the Margin & Labor test kitchen")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
