"""Search from anywhere, the savings and cost charts, and the what-if tools."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import current_user, manager
from app.auth.models import User
from app.db.session import get_db
from app.platform import flags
from app.restaurant.inventory import scope_for

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])


def _scope(db: Session, user: User) -> str:
    """The signed-in user's restaurant, with this feature switched on for it."""
    restaurant_id = scope_for(db, user)
    if restaurant_id is None:
        raise HTTPException(404, "Set up your restaurant first.")
    if not flags.enabled(db, restaurant_id, flags.V3_NAV):
        raise HTTPException(404, "The new navigation isn't switched on for this restaurant (Profile, Features).")
    return restaurant_id


from datetime import date  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from app.insights import engine  # noqa: E402


class SavingsWeek(BaseModel):
    week_start: date
    found: float
    acted: float
    found_to_date: float
    acted_to_date: float


class SavingsOut(BaseModel):
    weeks: list[SavingsWeek]
    found_to_date: float
    acted_to_date: float
    open_this_week: float


class SearchHit(BaseModel):
    kind: str
    title: str
    subtitle: str
    href: str


@router.get("/savings", response_model=SavingsOut)
def read_savings(user: User = Depends(manager), db: Session = Depends(get_db)):
    """What the digest has found week by week, and how much of it was acted on."""
    return engine.savings(db, _scope(db, user))


@router.get("/prime-cost")
def read_prime_cost(user: User = Depends(manager), db: Session = Depends(get_db)) -> list[dict]:
    """Food and labor as a share of sales, by week."""
    return engine.prime_cost(db, _scope(db, user))


@router.get("/spend")
def read_spend(days: int = 28, user: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """Purchases by supplier and by category over the last `days`, beside the period before."""
    if days not in (7, 28, 56):
        raise HTTPException(422, "days must be 7, 28 or 56.")
    return engine.spend(db, _scope(db, user), days)


@router.get("/stock-outlook")
def read_stock_outlook(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Each stocked item's use a day, lead time and orders on the way: what the par calculator draws from."""
    return engine.stock_outlook(db, _scope(db, user))


@router.get("/switch-options")
def read_switch_options(user: User = Depends(manager), db: Session = Depends(get_db)) -> list[dict]:
    """Items more than one supplier sells, with each supplier's latest price a pack."""
    return engine.switch_options(db, _scope(db, user))


@router.get("/search", response_model=list[SearchHit])
def run_search(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Items, suppliers, invoices and dishes whose name contains `q`."""
    return engine.search(db, _scope(db, user), q[:80])
