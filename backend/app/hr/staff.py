"""Whose team is whose, which login is which employee, and who sees what.

Scope. Staff and shifts belong to one restaurant, like its stock (see
app/restaurant/inventory.py): people only see their own restaurant's, and
another restaurant's records read as missing.

Linking. An employee's login is the account at the same restaurant whose
email matches the email on their profile, compared case-insensitively.
Nothing is stored: a manager fixing a typo in the address links the right
account on the next request.

Visibility. Owners and admins manage HR and see everything. Everyone else
sees coworkers' names and roles, their own profile (without the manager's
notes), and only the published schedule.
"""

from sqlalchemy import func, update
from sqlalchemy.orm import Query, Session

from app.auth.models import User
from app.restaurant.models import Restaurant, Shift, Staff

#: What an employee may change on their own profile.
SELF_EDITABLE = frozenset({"phone", "emergency_contact"})


def normalize_email(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    return value or None


def staff_query(db: Session, restaurant_id: str | None) -> Query:
    # `== None` is SQLAlchemy's IS NULL: no restaurant sees only unclaimed staff.
    return db.query(Staff).filter(Staff.restaurant_id == restaurant_id)


def staff_in(db: Session, restaurant_id: str | None) -> list[Staff]:
    return staff_query(db, restaurant_id).order_by(Staff.name).all()


def person_in(db: Session, restaurant_id: str | None, staff_id: str) -> Staff | None:
    """One employee, only if they're this restaurant's -- another's reads as missing."""
    return staff_query(db, restaurant_id).filter(Staff.id == staff_id).first()


def shift_query(db: Session, restaurant_id: str | None, *, published_only: bool = False) -> Query:
    query = db.query(Shift).filter(Shift.restaurant_id == restaurant_id)
    if published_only:
        query = query.filter(Shift.published_at.is_not(None))
    return query


def shift_in(db: Session, restaurant_id: str | None, shift_id: int) -> Shift | None:
    return shift_query(db, restaurant_id).filter(Shift.id == shift_id).first()


def email_in_use(db: Session, restaurant_id: str | None, email: str, exclude_staff_id: str | None = None) -> bool:
    """Two profiles with one address would both claim the same login."""
    query = staff_query(db, restaurant_id).filter(func.lower(Staff.email) == email)
    if exclude_staff_id:
        query = query.filter(Staff.id != exclude_staff_id)
    return db.query(query.exists()).scalar()


def account_emails(db: Session, restaurant_id: str | None) -> set[str]:
    """Addresses of everyone who can sign in to this restaurant."""
    return {email.lower() for (email,) in db.query(User.email).filter(User.restaurant_id == restaurant_id)}


def profile_for(db: Session, user: User, restaurant_id: str | None) -> Staff | None:
    """The employee `user` is, if a manager has put their email on a profile."""
    return staff_query(db, restaurant_id).filter(func.lower(Staff.email) == user.email.lower()).first()


def view(person: Staff, viewer: User, accounts: set[str]) -> dict:
    """`person` as `viewer` may see them."""
    is_me = bool(person.email) and person.email.lower() == viewer.email.lower()
    shown = {"id": person.id, "name": person.name, "role": person.role, "is_me": is_me}
    if viewer.is_manager or is_me:
        shown.update(
            email=person.email, phone=person.phone, emergency_contact=person.emergency_contact,
            hire_date=person.hire_date,
        )
    if viewer.is_manager:
        shown.update(notes=person.notes, has_account=bool(person.email) and person.email.lower() in accounts)
    return shown


def claim_unassigned(db: Session, restaurant: Restaurant) -> None:
    """Give staff and shifts that belong to no restaurant to `restaurant`.

    Run when setup creates the restaurant, so a team seeded before then
    becomes its team rather than disappearing from view.
    """
    for model in (Staff, Shift):
        db.execute(update(model).where(model.restaurant_id.is_(None)).values(restaurant_id=restaurant.id))
