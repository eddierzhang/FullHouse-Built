"""What the demo carries beyond `app.demo`: the Margin & Labor modules switched on in the demo restaurant, from its
own data (`fill_demo`), and an empty restaurant with every feature on (`seed_empty`). Signing in to the demo offers
both: pages filled in, or an empty slate.

`app.demo.reset_now` starts `fill_in_background` after the demo restaurant is ready, so the site answers at once.
Corner Grill (`seed_sample`) is a separate small sample restaurant, used only by `python dev.py margin-labor`.
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


def fill_in_background(solve_seconds: int = 6) -> threading.Thread:
    """After a demo reset: switch the Margin & Labor modules on in the demo restaurant (`fill_demo`) and add the empty
    restaurant beside it, on a thread and a session of their own. A failure is logged and leaves the demo as it was."""
    def work() -> None:
        from app.db.session import SessionLocal

        with _seeding:
            db = SessionLocal()
            try:
                started = datetime.now(timezone.utc)
                seed_empty(db)
                fill_demo(db, solve_seconds, checkpoint=db.commit)
                db.commit()
                logger.info("demo restaurant filled in %.1fs", (datetime.now(timezone.utc) - started).total_seconds())
            except Exception:  # noqa: BLE001 - the demo restaurant must stay up whatever happens here
                logger.exception("filling the demo restaurant failed; it runs without the margin and labor modules")
                db.rollback()
            finally:
                db.close()

    thread = threading.Thread(target=work, name="fill-demo", daemon=True)
    thread.start()
    return thread


def empty_ready(db: Session) -> bool:
    from app.auth.models import User

    return db.query(User.id).filter(User.email == EMPTY[0]).first() is not None


# --- the demo restaurant, with the Margin & Labor modules live ------------------------------------------

#: The demo restaurant's team and rota, on its own clock: (name, title, role key, start, end, rest days with Monday = 0,
#: hourly rate). Each role has a lunch person and a dinner person. Six are the demo's own staff; the other
#: five join here, because six people cannot cover five roles from open to close at this restaurant's sales. Friday's
#: host stand at dinner is left open: it is the gap the demo's schedule proposal offers to fill.
ROTA = (
    ("Priya Shah", "Sous Chef", "head_chef", "11:00", "16:30", (1,), "26.00"),
    ("Maria Gonzalez", "Head Chef", "head_chef", "16:30", "22:00", (0,), "32.00"),
    ("Marcus Bell", "Line Cook", "line_cook", "11:00", "16:30", (2,), "20.00"),
    ("Jordan Lee", "Line Cook", "line_cook", "16:30", "22:00", (1,), "22.00"),
    ("Alex Rivera", "Server", "server", "11:00", "16:30", (2,), "15.00"),
    ("Chris Nguyen", "Server", "server", "16:30", "22:00", (0,), "15.00"),
    ("Nina Ortiz", "Host", "host", "11:00", "16:30", (3,), "15.00"),
    ("Sam Patel", "Host", "host", "16:30", "22:00", (1, 4), "15.00"),
    ("Leo Park", "Bartender", "bartender", "11:00", "16:30", (0,), "17.00"),
    ("Taylor Brooks", "Bartender", "bartender", "16:30", "22:00", (1,), "17.00"),
    # A part-timer for the busy end of the week, who can serve or take the host stand.
    ("Dana Kim", "Server", "server", "16:30", "22:00", (0, 1, 2, 3), "15.00"),
    ("Omar Haddad", "Line Cook", "line_cook", "16:30", "22:00", (0, 1, 2), "20.00"),
    ("Bea Lindqvist", "Bartender", "bartender", "11:00", "16:30", (1, 2, 3), "17.00"),
    ("Ravi Menon", "Line Cook", "line_cook", "11:00", "16:30", (0, 1, 2, 3), "20.00"),
)
#: Hired two weeks ago: on the team for the schedule being drafted, with only two weeks of clock-ins behind them.
NEW_HIRES = ("Omar Haddad", "Bea Lindqvist", "Ravi Menon")
#: What else each person can cover when the schedule is drafted, beside their own role.
ALSO = {"Maria Gonzalez": ("line_cook",), "Priya Shah": ("line_cook",), "Marcus Bell": ("head_chef",), "Jordan Lee": ("head_chef",), "Nina Ortiz": ("server",), "Sam Patel": ("server",),
        "Leo Park": ("server", "host"), "Taylor Brooks": ("server", "host"), "Dana Kim": ("host", "bartender"), "Omar Haddad": ("head_chef",), "Ravi Menon": ("head_chef",), "Bea Lindqvist": ("server", "host"), "Alex Rivera": ("host",), "Chris Nguyen": ("host", "bartender")}


def fill_demo(db: Session, solve_seconds: int = 6, checkpoint=None) -> str | None:
    """Switch Menu costs, Usage, Ordering and prep and Labor on in the demo restaurant and give them what they
    need, all taken from what the demo already holds so every page agrees: order-level sales are the demo's own
    orders, clock-ins and shifts follow one rota, daily labor is those hours at each person's rate, and delivery
    days are the suppliers' own calendars. None if the demo restaurant is not there."""
    from datetime import time, timedelta
    from decimal import Decimal

    from app.auth.models import User
    from app.demo import DEMO_EMAIL
    from app.margin_labor import demand, labor, labor_read, menu_costs, schedule, usage
    from app.margin_labor.business import zone
    from app.margin_labor.models import (DishCost, EmployeeWage, LaborPunch, LaborRules, SalesOrder, SalesOrderLine, Schedule,
                                         ScheduleShift, SupplierSchedule)
    from app.ordering.models import SupplierCalendar
    from app.platform import flags
    from app.recipes.models import Recipe
    from app.restaurant.models import MenuItem, Order, OrderItem, Shift, Staff
    from app.sales import pnl
    from app.sales.models import LaborDay

    user = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if user is None:
        return None
    rid = user.restaurant_id
    step = checkpoint or (lambda: None)
    service = labor.Service(db, rid)
    local, today = zone(service.conf.timezone), service.today
    now_local = datetime.now(timezone.utc).astimezone(local)

    # Delivery days: each supplier's own calendar, an order day paired with the delivery day that follows it.
    for calendar in db.query(SupplierCalendar).filter(SupplierCalendar.restaurant_id == rid):
        for order_day in calendar.order_days or []:
            ahead = sorted(calendar.delivery_days or [], key=lambda d: (d - order_day - 1) % 7)
            if ahead:
                db.add(SupplierSchedule(restaurant_id=rid, supplier_id=calendar.supplier_id, order_cutoff_weekday=order_day, order_cutoff_time=calendar.cutoff_time,
                                        delivery_weekday=ahead[0], minimum_order_amount=None if calendar.minimum_dollars is None else Decimal(str(calendar.minimum_dollars))))

    # Order-level sales: the demo's own orders, line for line, at the same time of day on the restaurant's clock.
    names = {m.id: m.name for m in db.query(MenuItem).filter(MenuItem.restaurant_id == rid)}
    pos = {r.name: r.pos_item_id for r in db.query(Recipe).filter(Recipe.restaurant_id == rid) if r.pos_item_id}
    from app.demo import _pos_id

    for count, order in enumerate(db.query(Order).filter(Order.restaurant_id == rid, Order.status == "completed").order_by(Order.created_at).all(), start=1):
        opened = datetime.combine(order.created_at.date(), order.created_at.time(), tzinfo=local)
        if opened > now_local:
            continue  # later today, on the restaurant's clock
        lines = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        net = sum((Decimal(str(line.unit_price)) * line.qty for line in lines), Decimal(0))
        sale = SalesOrder(restaurant_id=rid, connection_key="square", external_id=order.id, opened_at=opened.astimezone(timezone.utc),
                          closed_at=(opened + timedelta(minutes=40)).astimezone(timezone.utc), business_date=opened.date(), channel=order.channel,
                          covers=max(1, sum(line.qty for line in lines)), gross=net, net=net, status="closed")
        db.add(sale)
        db.flush()
        for number, line in enumerate(lines, start=1):
            dish = names[line.menu_item_id]
            db.add(SalesOrderLine(restaurant_id=rid, order_id=sale.id, line_no=number, business_date=sale.business_date, pos_item_id=pos.get(dish) or _pos_id(dish),
                                  pos_item_name=dish, qty=Decimal(line.qty), net=Decimal(str(line.unit_price)) * line.qty))
        if count % 300 == 0:
            step()
    step()

    # People: one rota for the clock-ins behind the standards, the shifts on the staff page and this week's schedule.
    from app.margin_labor.models import EmployeeProfile

    staff = {s.name: s for s in db.query(Staff).filter(Staff.restaurant_id == rid)}
    for name, title, role, _start, _end, _off, rate in ROTA:
        if name not in staff:
            staff[name] = Staff(restaurant_id=rid, name=name, role=title)
            db.add(staff[name])
            db.flush()
        db.add(EmployeeWage(restaurant_id=rid, staff_id=staff[name].id, hourly_rate=Decimal(rate)))
        db.add(EmployeeProfile(restaurant_id=rid, staff_id=staff[name].id, role_keys=[role, *ALSO.get(name, ())]))
    # The bar has nothing on the menu to be measured by, so its standard is the owner's own: one bartender for 40 covers an hour.
    from app.margin_labor.models import LaborStandard

    db.add(LaborStandard(restaurant_id=rid, role_key="bartender", driver_metric="covers", driver_filter={"channels": ["dine_in"]}, units_per_labor_hour=Decimal("40"),
                         min_on_floor=0, source="owner", effective_from=today - timedelta(days=56)))
    db.add(LaborRules(restaurant_id=rid, weekly_overtime_hours=40, daily_overtime_hours=8, min_rest_hours=10, min_shift_hours=4, max_shift_hours=10,
                      max_hours_per_day=10, max_consecutive_days=7, break_after_hours=6, break_minutes=30, min_paid_hours=1, advance_notice_days=3, allow_on_call=False,
                      confirmed_by="the owner", confirmed_at=datetime.now(timezone.utc)))

    def span(day, start: str, end: str):
        at = lambda clock: datetime.combine(day, time(*(int(p) for p in clock.split(":"))), tzinfo=local)  # noqa: E731
        return at(start), at(end)

    worked: dict = {}
    first = today - timedelta(days=56)
    for back in range(56, 0, -1):  # eight weeks: a role needs four full weeks of clock-ins before its standard is learned
        day = today - timedelta(days=back)
        for name, _title, role, start, end, off, rate in ROTA:
            if day.weekday() in off or (name in NEW_HIRES and back > 14):
                continue
            begin, finish = span(day, start, end)
            db.add(LaborPunch(restaurant_id=rid, employee_id=staff[name].id, employee_name=name, role_key=role, clock_in=begin.astimezone(timezone.utc),
                              clock_out=finish.astimezone(timezone.utc), business_date=day, source="gusto", external_id=f"demo-{day.isoformat()}-{staff[name].id}"))
            hours = (finish - begin).total_seconds() / 3600
            cell = worked.setdefault(day, [0.0, 0.0])
            cell[0] += hours
            cell[1] += hours * float(rate)
    # Daily labor is those hours at those rates, so prime cost and the Labor page are one set of books.
    for row in db.query(LaborDay).filter(LaborDay.restaurant_id == rid, LaborDay.date >= first):
        if row.date in worked:
            row.hours, row.dollars = round(worked[row.date][0], 2), round(worked[row.date][1], 2)
    # The staff page's shifts for the week ahead, and this week's published schedule, from the same rota.
    db.query(Shift).filter(Shift.restaurant_id == rid, Shift.date > today).delete()
    monday = today - timedelta(days=today.weekday())
    published = Schedule(restaurant_id=rid, week_of=monday, status="published", version=1, published_at=datetime.now(timezone.utc), published_by="the owner",
                         generated_at=datetime.now(timezone.utc))
    db.add(published)
    db.flush()
    for ahead in range(0, 14):
        day = monday + timedelta(days=ahead)
        for name, _title, role, start, end, off, rate in ROTA:
            if day.weekday() in off:
                continue
            begin, finish = span(day, start, end)
            if day < monday + timedelta(days=7):
                db.add(ScheduleShift(restaurant_id=rid, schedule_id=published.id, staff_id=staff[name].id, role_key=role, business_date=day,
                                     start_at=begin.astimezone(timezone.utc), end_at=finish.astimezone(timezone.utc)))
            if today < day <= today + timedelta(days=7):
                db.add(Shift(restaurant_id=rid, staff_id=staff[name].id, date=day, start_time=begin.time(), end_time=finish.time(), role=staff[name].role,
                             published_at=datetime.now(timezone.utc)))
    db.flush()
    week = pnl.last_complete_week(today)
    for back in range(4):
        pnl.compute_week(db, rid, week - timedelta(days=7 * back))
    step()

    # The modules on. Channels stays off: it is out of the app for now.
    for flag in flags.ML_FLAGS:
        flags.set_flag(db, rid, flag, flag != flags.ML_CHANNELS)
    step()
    # The two recipes the agent proposed for the top sellers are confirmed, as an owner would: recipes then cover
    # enough of sales for Usage to report, and each dish has a cost.
    from app.agents.appliers import ApplyError, apply_action
    from app.agents.crud import mark_action_decided
    from app.agents.models import AgentAction
    from app.audit.context import Actor, actor_context

    for action in db.query(AgentAction).filter(AgentAction.restaurant_id == rid, AgentAction.action_type == "recipe", AgentAction.status == "pending").all():
        # The agent asks "Change any quantity before approving": the owner corrects the garlic, a clove's worth a plate, not a quarter pound.
        payload = dict(action.payload or {})
        payload["lines"] = [{**line, "qty": 0.02} if str(line.get("ingredient_text", "")).lower() == "garlic" and line.get("qty") == 0.25 else line for line in payload.get("lines", [])]
        action.payload = payload
        try:
            with actor_context(Actor(type="human", id=user.id, agent_run_id=action.run_id, agent_action_id=action.id, restaurant_id=rid)):
                mark_action_decided(db, action, "approved", applied_result=apply_action(db, action))
        except ApplyError:
            logger.warning("could not confirm the proposed recipe %s", (action.payload or {}).get("name"))
    step()
    menu_costs.recost(db, rid)
    step()
    # The Dishes list shows the same cost per plate as Menu costs.
    recipes = {r.id: r.name for r in db.query(Recipe).filter(Recipe.restaurant_id == rid)}
    by_name = {m.name: m for m in db.query(MenuItem).filter(MenuItem.restaurant_id == rid)}
    for cost in db.query(DishCost).filter(DishCost.restaurant_id == rid, DishCost.is_current.is_(True)):
        dish = by_name.get(recipes.get(cost.recipe_id, ""))
        if dish is not None:
            dish.cost = round(float(cost.plate_cost), 2)
    usage.run(db, rid)
    demand.forecast_items(db, rid)
    step()
    labor.forecast_labor(db, rid)
    # The owner's floor minimums: a chef and a server whenever the doors are open; the cook, host and bar come in on demand.
    for standard in db.query(LaborStandard).filter(LaborStandard.restaurant_id == rid):
        standard.min_on_floor = 1 if standard.role_key in ("head_chef", "server") else 0
    db.flush()
    labor.forecast_labor(db, rid)
    step()
    schedule.generate(db, rid, labor_read.default_week(today), seconds=solve_seconds)
    step()
    return rid
