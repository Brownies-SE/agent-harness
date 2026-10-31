import re
from datetime import datetime

from pydantic import Field, SecretStr, field_validator

from .base import StrictModel
from .chat import CurrentUser


class LoginCredentials(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(
            r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
            r"@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
            value,
        ):
            raise ValueError("invalid email")
        return value


class SetupCredentials(LoginCredentials):
    password: SecretStr = Field(min_length=12, max_length=1024)


class AuthStatus(StrictModel):
    setup_required: bool


class AuthSession(StrictModel):
    session_token: str
    expires_at: datetime
    user: CurrentUser
