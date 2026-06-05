"""Auth DTOs — signup, login, token issuance."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone_number: str = Field(min_length=7, max_length=20)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("Password must contain at least one letter and one digit")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class LogoutRequest(BaseModel):
    """If `refresh_token` is omitted, all of the user's refresh
    tokens are revoked (sign-out-everywhere)."""

    refresh_token: Optional[str] = Field(default=None, min_length=10)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access_token expires


class UserPublic(BaseModel):
    """Safe-to-expose user shape. No hashed_password, no BVN."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    email: str
    phone_number: str
    is_telegram_linked: bool
    created_at: datetime


class WalletBalance(BaseModel):
    balance: float
    currency: str
