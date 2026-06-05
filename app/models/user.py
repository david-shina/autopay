"""User model.

Sensitive data rules:
- No BVN here. BVN lives encrypted in `kyc_records` (one-to-one).
- `balance` is NUMERIC(14,2) — never float for money.
- Passwords are bcrypt-hashed (see app.core.security).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Column, Numeric, String
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    first_name: str = Field(max_length=100, nullable=False)
    last_name: str = Field(max_length=100, nullable=False)
    email: str = Field(
        sa_column=Column(String(255), nullable=False, unique=True, index=True),
    )
    phone_number: str = Field(
        sa_column=Column(String(20), nullable=False, unique=True, index=True),
    )
    hashed_password: str = Field(max_length=255, nullable=False)
    telegram_chat_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), unique=True, index=True, nullable=True),
    )
    is_telegram_linked: bool = Field(default=False, nullable=False)
    balance: Decimal = Field(
        default=Decimal("0.00"),
        sa_column=Column(Numeric(14, 2), nullable=False, server_default="0"),
    )
    currency: str = Field(default="NGN", max_length=3, nullable=False)
    address: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"onupdate": datetime.now},
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email!r}>"
