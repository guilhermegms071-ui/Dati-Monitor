"""Authentication request/response schemas."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.validators import check_password_policy


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class MeResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str
    role_name: str
    reseller_id: uuid.UUID
    reseller_name: str
    customer_id: uuid.UUID | None
    permissions: list[str]
    totp_enabled: bool
    must_change_password: bool
    preferences: dict[str, Any]
    last_login_at: datetime | None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - tipo do token, não é senha
    expires_at: datetime
    limited: Literal["password_change_required", "totp_setup_required"] | None = None
    user: MeResponse


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(max_length=256)

    @field_validator("new_password")
    @classmethod
    def _policy(cls, v: str) -> str:
        return check_password_policy(v)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(max_length=256)

    @field_validator("new_password")
    @classmethod
    def _policy(cls, v: str) -> str:
        return check_password_policy(v)


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class TotpCodeRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class TotpDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(pattern=r"^\d{6}$")


class PreferencesUpdate(BaseModel):
    """Merge parcial nas preferências da interface (ex.: colunas da tela de parque)."""

    preferences: dict[str, Any]

    @field_validator("preferences")
    @classmethod
    def _size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(str(v)) > 20_000:  # noqa: PLR2004
            raise ValueError("preferências grandes demais")
        return v
