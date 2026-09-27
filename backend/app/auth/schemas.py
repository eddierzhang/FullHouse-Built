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


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)

    _email = field_validator("email")(_normalise_email)


class SignupIn(BaseModel):
    name: str = Field(max_length=100)
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)

    _email = field_validator("email")(_normalise_email)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter your name.")
        return value

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Use at least 8 characters.")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    role: str
    restaurant_id: str | None
    created_at: datetime


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
