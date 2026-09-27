"""The restaurant profile, one schema per setup step.

Setup and the Profile page save through the same schemas, so a value
the wizard would refuse can't be slipped in later through settings.
"""

import re
from datetime import datetime, time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Setup order. Each is a step in the wizard and a card in Settings.
SECTIONS = ("details", "hours", "inventory", "staff", "scheduling")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = dict(zip(WEEKDAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^[0-9+()\-.\s]{5,40}$")


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("This field is required.")
    return value


def _optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _website(value: str | None) -> str | None:
    """A bare "casaluna.com" gets https:// in front, so it works as a link."""
    value = _optional(value)
    if value is None:
        return None
    if "://" not in value:
        value = f"https://{value}"
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
    except ValueError:
        host, parts = "", None
    if (
        parts is None
        or parts.scheme not in ("http", "https")
        or "." not in host.strip(".")
        or any(c.isspace() for c in value)
        or len(value) > 255
    ):
        raise ValueError("Enter a web address, like https://example.com, or leave it blank.")
    return value


def _clock(value: str | None) -> str | None:
    """"HH:MM", 24-hour. Normalised so "9:00" and "09:00" are the same value."""
    if value in (None, ""):
        return None
    try:
        return time.fromisoformat(value.strip().zfill(5)).strftime("%H:%M")
    except ValueError:
        raise ValueError(f"{value!r} is not a time. Use 24-hour HH:MM, like 09:30.") from None


class DetailsIn(BaseModel):
    name: str = Field(max_length=120)
    street: str = Field(max_length=200)
    city: str = Field(max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str = Field(max_length=100)
    phone: str = Field(max_length=40)
    email: str | None = Field(default=None, max_length=254)
    website: str | None = Field(default=None, max_length=255)

    _required_fields = field_validator("name", "street", "city", "country")(_required)
    _optional_fields = field_validator("region", "postal_code")(_optional)
    _website = field_validator("website")(_website)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        value = value.strip()
        if not _PHONE.match(value) or sum(c.isdigit() for c in value) < 5:
            raise ValueError("Enter a phone number, like +1 555 123 4567.")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = _optional(value)
        if value is not None and not _EMAIL.match(value):
            raise ValueError("Enter a valid email address, or leave it blank.")
        return value.lower() if value else None


class DayHours(BaseModel):
    closed: bool = False
    open: str | None = None
    close: str | None = None

    _times = field_validator("open", "close")(_clock)

    @model_validator(mode="after")
    def _both_times(self) -> "DayHours":
        if self.closed:
            self.open = self.close = None
        elif not self.open or not self.close:
            raise ValueError("Give an opening and a closing time, or mark the day closed.")
        elif self.open == self.close:
            raise ValueError("Opening and closing times can't be the same.")
        return self


class HoursIn(BaseModel):
    timezone: str
    #: A closing time earlier than the opening time runs past midnight.
    opening_hours: dict[str, DayHours]

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value.strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"{value!r} is not a known timezone, like America/New_York.") from None
        return value.strip()

    @field_validator("opening_hours")
    @classmethod
    def _week(cls, value: dict[str, DayHours]) -> dict[str, DayHours]:
        if set(value) != set(WEEKDAYS):
            raise ValueError(f"Give hours for every day: {', '.join(WEEKDAYS)}.")
        if all(day.closed for day in value.values()):
            raise ValueError("The restaurant needs to be open at least one day a week.")
        for index, name in enumerate(WEEKDAYS):
            today, following = value[name], WEEKDAYS[(index + 1) % 7]
            tomorrow = value[following]
            # "HH:MM" strings compare in clock order.
            runs_late = not today.closed and today.close < today.open
            if runs_late and not tomorrow.closed and tomorrow.open < today.close:
                raise ValueError(
                    f"{DAY_NAMES[name]}'s hours run to {today.close}, past {DAY_NAMES[following]}'s "
                    f"opening at {tomorrow.open}."
                )
        return {day: value[day] for day in WEEKDAYS}


class InventoryIn(BaseModel):
    #: Extra days of stock to hold beyond the supplier's lead time.
    safety_days: int = Field(default=1, ge=0, le=30)
    #: Lead time a new supplier starts with.
    default_lead_time_days: int = Field(default=2, ge=0, le=60)
    #: Local time for the Inventory Agent's daily stock check; null for none.
    daily_stock_check: str | None = None

    _check = field_validator("daily_stock_check")(_clock)


class StaffIn(BaseModel):
    staff_roles: list[str] = Field(max_length=30)

    @field_validator("staff_roles")
    @classmethod
    def _roles(cls, value: list[str]) -> list[str]:
        roles, seen = [], set()
        for role in value:
            role = role.strip()
            if not role:
                continue
            if len(role) > 64:
                raise ValueError(f"{role[:20]}… is too long; keep roles under 64 characters.")
            if role.lower() not in seen:
                seen.add(role.lower())
                roles.append(role)
        if not roles:
            raise ValueError("Add at least one staff role.")
        return roles


class SchedulingIn(BaseModel):
    max_shift_hours: float = Field(default=10, gt=0, le=24)
    min_rest_hours: float = Field(default=10, ge=0, le=24)
    max_weekly_hours: float = Field(default=40, gt=0, le=100)
    min_staff_per_open_day: int = Field(default=2, ge=0, le=100)

    @model_validator(mode="after")
    def _consistent(self) -> "SchedulingIn":
        if self.max_weekly_hours < self.max_shift_hours:
            raise ValueError("Weekly hours can't be less than a single shift's maximum.")
        return self


class RestaurantDetailsIn(DetailsIn, HoursIn):
    """The Profile page's Restaurant Details tab: details and hours, saved together.

    Built from the setup steps' own schemas, so it accepts exactly what they do.
    """

    #: The `updated_at` the editor loaded. When given and no longer current,
    #: someone else saved in between, and the save is refused rather than
    #: silently overwriting their change.
    expected_updated_at: datetime | None = None


SECTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "details": DetailsIn,
    "hours": HoursIn,
    "inventory": InventoryIn,
    "staff": StaffIn,
    "scheduling": SchedulingIn,
}


class RestaurantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str | None
    street: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    country: str | None
    phone: str | None
    email: str | None
    website: str | None
    timezone: str
    opening_hours: dict[str, DayHours] | None
    inventory_settings: InventoryIn | None
    staff_roles: list[str] | None
    scheduling_rules: SchedulingIn | None
    completed_steps: list[str]
    setup_completed_at: datetime | None
    updated_at: datetime


class Editor(BaseModel):
    id: str
    name: str
    email: str


class RestaurantView(RestaurantOut):
    """The restaurant as one particular user may see it."""

    #: Who saved last. Owners and admins only; null for everyone else, and
    #: when that account no longer exists.
    updated_by: Editor | None = None
    #: Whether this user may change the profile: owners and admins.
    can_edit: bool = False
