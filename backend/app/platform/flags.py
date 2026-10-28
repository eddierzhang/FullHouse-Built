"""Per-restaurant feature flags. Phases ship dark and are turned on per customer.

    from app.platform import flags
    if flags.enabled(db, restaurant_id, flags.V2_CONNECT): ...

Owners switch them on the Profile page; operators with `python -m app.platform.flags enable <email-or-id> <flag>`.
"""

from __future__ import annotations

import sys

from sqlalchemy.orm import Session

from app.platform.models import RestaurantFlag

V2_CONNECT = "v2_connect"
V2_DOCUMENTS = "v2_documents"
V2_ORDERS = "v2_orders"
V2_CALENDAR = "v2_calendar"

#: Every flag the app knows, with what it turns on. Unknown flags are refused.
FLAGS: dict[str, str] = {
    V2_CONNECT: "Connections (POS, payroll, inbound mail) and the prime-cost line in the digest.",
    V2_DOCUMENTS: "Documents, agreed prices, recipes and plate cost, menu-margin findings.",
    V2_ORDERS: "Purchase-order drafts from the agent, forecasting and prep sheets.",
    V2_CALENDAR: "Licenses, permits, inspections and warranties on a calendar, with 'Coming due' in the digest.",
}
#: Settings that live in the same table but are numbers, not switches: agent runs per day.
AGENT_RUNS_PER_DAY = "agent_runs_per_day"
DEFAULT_AGENT_RUNS_PER_DAY = 50


def enabled(db: Session, restaurant_id: str | None, flag: str) -> bool:
    if restaurant_id is None:
        return False
    row = db.query(RestaurantFlag).filter(RestaurantFlag.restaurant_id == restaurant_id, RestaurantFlag.flag == flag).first()
    return bool(row and row.enabled)


def all_for(db: Session, restaurant_id: str | None) -> dict[str, bool]:
    """Every known flag and whether it is on for this restaurant."""
    on = set()
    if restaurant_id is not None:
        on = {r.flag for r in db.query(RestaurantFlag).filter(RestaurantFlag.restaurant_id == restaurant_id, RestaurantFlag.enabled)}
    return {flag: flag in on for flag in FLAGS}


def set_flag(db: Session, restaurant_id: str, flag: str, on: bool) -> RestaurantFlag:
    if flag not in FLAGS:
        raise ValueError(f"Unknown flag {flag!r}. Known: {', '.join(FLAGS)}.")
    row = db.query(RestaurantFlag).filter(RestaurantFlag.restaurant_id == restaurant_id, RestaurantFlag.flag == flag).first()
    if row is None:
        row = RestaurantFlag(restaurant_id=restaurant_id, flag=flag, enabled=on)
        db.add(row)
    else:
        row.enabled = on
    db.flush()
    return row


def enable_all(db: Session, restaurant_id: str) -> None:
    """Every phase on: the demo, and a site that wants all of it."""
    for flag in FLAGS:
        set_flag(db, restaurant_id, flag, True)


def _restaurant_id(db: Session, email_or_id: str) -> str:
    from app.auth.models import User
    from app.restaurant.models import Restaurant

    if db.get(Restaurant, email_or_id) is not None:
        return email_or_id
    user = db.query(User).filter(User.email == email_or_id.lower()).first()
    if user is None or not user.restaurant_id:
        raise SystemExit(f"No restaurant for {email_or_id!r}: give a restaurant id or an owner's email.")
    return user.restaurant_id


def main(argv: list[str]) -> int:
    """`enable|disable <email-or-restaurant-id> <flag>`, or `list <email-or-restaurant-id>`."""
    from app.audit.context import SYSTEM_ACTOR, actor_context
    from app.db.session import SessionLocal

    if len(argv) < 2 or argv[0] not in ("enable", "disable", "list"):
        print(__doc__)
        print("Flags:\n" + "\n".join(f"  {k}: {v}" for k, v in FLAGS.items()))
        return 2
    db = SessionLocal()
    try:
        restaurant_id = _restaurant_id(db, argv[1])
        if argv[0] == "list":
            for flag, on in all_for(db, restaurant_id).items():
                print(f"{'on ' if on else 'off'}  {flag}")
            return 0
        if len(argv) < 3:
            print("Which flag? " + ", ".join(FLAGS))
            return 2
        with actor_context(SYSTEM_ACTOR):
            set_flag(db, restaurant_id, argv[2], argv[0] == "enable")
            db.commit()
        print(f"{argv[2]} {'on' if argv[0] == 'enable' else 'off'} for {restaurant_id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    from app.bootstrap import setup

    setup()
    sys.exit(main(sys.argv[1:]))
