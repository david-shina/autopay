"""KYC (Know Your Customer) record — holds encrypted BVN.

The BVN (Bank Verification Number) is a highly sensitive Nigerian government-issued
identifier. Storage rules:
- Plaintext BVN is **never** stored.
- `bvn_ciphertext`  : Fernet-encrypted bytes (reversible only with the app's key)
- `bvn_hash`        : HMAC-SHA256 with app pepper, for uniqueness lookups
- `bvn_last4`       : last 4 digits, for safe display ("******1234")
- `bvn_validated`   : True once the provider confirms it against BVN registry
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey, LargeBinary, String
from sqlmodel import Field, SQLModel


class KycRecord(SQLModel, table=True):
    __tablename__ = "kyc_records"

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
            index=True,
        ),
    )
    bvn_ciphertext: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    bvn_last4: str = Field(max_length=4, nullable=False)
    bvn_hash: str = Field(
        sa_column=Column(String(64), nullable=False, unique=True, index=True),
    )
    bvn_validated: bool = Field(default=False, nullable=False)
    validated_at: Optional[datetime] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
