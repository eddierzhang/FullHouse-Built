import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Deliberately loose: the only reliable check of an address is mailing it,
# so this just catches typos like a missing "@" or domain.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalise_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL.match(value):
        raise ValueError("Enter a valid email address.")
    return value


def _valid_password(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Use at least 8 characters.")
    return value


def _valid_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter your name.")
    return value


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)

    _email = field_validator("email")(_normalise_email)


class SignupIn(BaseModel):
    name: str = Field(max_length=100)
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)

    _email = field_validator("email")(_normalise_email)

    _name = field_validator("name")(_valid_name)
    _password = field_validator("password")(_valid_password)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    role: str
    restaurant_id: str | None
    created_at: datetime
    #: Set while an owner or admin has turned the account off.
    disabled_at: datetime | None = None


class RoleIn(BaseModel):
    #: "owner" is not on offer: ownership comes from setting the restaurant up.
    role: Literal["admin", "member"]


class DemoCredentials(BaseModel):
    email: str
    password: str


class AuthOptions(BaseModel):
    signup_enabled: bool
    #: Shown on the login page of the public demo; null everywhere else.
    demo: DemoCredentials | None = None


class InviteIn(BaseModel):
    role: Literal["admin", "member"] = "member"
    #: Optional: when given, only this address can accept the invite.
    email: str | None = Field(default=None, max_length=254)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return _normalise_email(value) if value and value.strip() else None


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    email: str | None
    created_at: datetime
    expires_at: datetime


class InviteCreated(InviteOut):
    #: Shown once: only its hash is stored, so it can't be looked up again.
    token: str


class InvitePreview(BaseModel):
    restaurant_name: str | None
    role: str
    email: str | None


class AcceptInviteIn(BaseModel):
    name: str = Field(max_length=100)
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)

    _email = field_validator("email")(_normalise_email)
    _name = field_validator("name")(_valid_name)
    _password = field_validator("password")(_valid_password)


class ResetLink(BaseModel):
    token: str
    expires_at: datetime


class ResetPreview(BaseModel):
    name: str
    email: str


class NewPasswordIn(BaseModel):
    password: str = Field(max_length=256)

    _password = field_validator("password")(_valid_password)


class SecurityEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    type: str
    label: str
    email: str | None
    actor_email: str | None
    ip: str | None
    user_agent: str | None
    detail: dict | None
