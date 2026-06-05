"""Transaction model — every wallet credit/debit the app makes.

Provider-agnostic: `provider` ('paystack' | future) + `provider_reference`
(unique) replace the MVP's hardcoded `payaza_reference`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey, Numeric, String
from sqlmodel import Field, SQLModel


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
    )
    bill_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("bills.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── What kind ──
    type: str = Field(max_length=10, nullable=False)  # 'credit' | 'debit'
    amount: Decimal = Field(sa_column=Column(Numeric(14, 2), nullable=False))
    fee: Decimal = Field(
        default=Decimal("0.00"),
        sa_column=Column(Numeric(14, 2), nullable=False, server_default="0"),
    )
    currency: str = Field(default="NGN", max_length=3, nullable=False)

    # ── Tracking ──
    status: str = Field(
        default="pending",
        max_length=20,
        nullable=False,
        index=True,
    )
    provider: str = Field(default="paystack", max_length=32, nullable=False)
    provider_reference: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), unique=True, index=True, nullable=True),
    )
    retry_count: int = Field(default=0, nullable=False)
    failure_reason: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    narration: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))

    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"onupdate": datetime.now},
        nullable=False,
    )
