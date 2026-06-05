"""Virtual Account (DVA) — one per user.

Provider-agnostic: the `provider` field lets you mix Paystack + future
providers. `provider_account_reference` is the gateway's reference for
this account; `account_number` is what users actually transfer to.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey, String
from sqlmodel import Field, SQLModel


class VirtualAccount(SQLModel, table=True):
    __tablename__ = "virtual_accounts"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
    )
    provider: str = Field(default="paystack", max_length=32, nullable=False)
    provider_account_reference: str = Field(
        sa_column=Column(String(128), nullable=False, unique=True, index=True),
    )
    account_number: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32), unique=True, index=True, nullable=True),
    )
    account_name: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    bank_name: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    currency: str = Field(default="NGN", max_length=3, nullable=False)
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
