"""Bill model — vendor invoice to be paid on the user's behalf."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Numeric, String
from sqlmodel import Field, SQLModel


class Bill(SQLModel, table=True):
    __tablename__ = "bills"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    vendor_name: str = Field(max_length=255, nullable=False)
    account_number: Optional[str] = Field(default=None, sa_column=Column(String(32), nullable=True))
    bank_code: Optional[str] = Field(default=None, sa_column=Column(String(16), nullable=True))
    bank_name: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True))
    amount: Decimal = Field(sa_column=Column(Numeric(14, 2), nullable=False))
    currency: str = Field(default="NGN", max_length=3, nullable=False)
    due_date: datetime = Field(nullable=False, index=True)
    status: str = Field(default="pending", max_length=20, nullable=False, index=True)
    is_recurring: bool = Field(default=False, nullable=False)
    recurrence_interval: Optional[str] = Field(default=None, sa_column=Column(String(16), nullable=True))
    next_recurrence_date: Optional[datetime] = Field(default=None, nullable=True)
    retry_count: int = Field(default=0, nullable=False)
    max_retries: int = Field(default=3, nullable=False)
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"onupdate": datetime.now},
        nullable=False,
    )
