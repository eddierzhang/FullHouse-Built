from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.deps import current_user
from app.auth.models import User
from app.db.session import get_db
from app.restaurant import profile
from app.restaurant.profile_schemas import (
    DetailsIn,
    HoursIn,
    InventoryIn,
    RestaurantDetailsIn,
    RestaurantView,
    SchedulingIn,
    StaffIn,
)

router = APIRouter(prefix="/api/v1/restaurant-profile", tags=["restaurant profile"])


def editor(user: User = Depends(current_user)) -> User:
    """Owners and admins; everyone else can look but not change."""
    if not user.is_manager:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can change the restaurant's details.")
    return user


@router.get("", response_model=RestaurantView | None)
def get_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The caller's restaurant, finished or not; null before the first setup step is saved."""
    restaurant = profile.restaurant_for(db, user)
    return profile.view(db, restaurant, user) if restaurant else None


@router.put("/restaurant-details", response_model=RestaurantView)
def save_restaurant_details(body: RestaurantDetailsIn, user: User = Depends(editor), db: Session = Depends(get_db)):
    """The Profile page's Restaurant Details tab: details, timezone and hours, all or nothing."""
    try:
        restaurant = profile.save_restaurant_details(db, user, body)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except profile.EditConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return profile.view(db, restaurant, user)


# One route per step rather than one taking a section name: each gets its
# own typed body, and so its own validation errors.


@router.put("/details", response_model=RestaurantView)
def save_details(body: DetailsIn, user: User = Depends(editor), db: Session = Depends(get_db)):
    return profile.view(db, profile.save_section(db, user, "details", body), user)


@router.put("/hours", response_model=RestaurantView)
def save_hours(body: HoursIn, user: User = Depends(editor), db: Session = Depends(get_db)):
    return profile.view(db, profile.save_section(db, user, "hours", body), user)


@router.put("/inventory", response_model=RestaurantView)
def save_inventory(body: InventoryIn, user: User = Depends(editor), db: Session = Depends(get_db)):
    return profile.view(db, profile.save_section(db, user, "inventory", body), user)


@router.put("/staff", response_model=RestaurantView)
def save_staff(body: StaffIn, user: User = Depends(editor), db: Session = Depends(get_db)):
    return profile.view(db, profile.save_section(db, user, "staff", body), user)


@router.put("/scheduling", response_model=RestaurantView)
def save_scheduling(body: SchedulingIn, user: User = Depends(editor), db: Session = Depends(get_db)):
    return profile.view(db, profile.save_section(db, user, "scheduling", body), user)


@router.post("/complete", response_model=RestaurantView)
def complete(user: User = Depends(editor), db: Session = Depends(get_db)):
    try:
        return profile.view(db, profile.complete_setup(db, user), user)
    except profile.SetupIncomplete as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
