"""Refresh token — for JWT auth (lands in Chunk 3).

The actual JWT is short-lived; refresh tokens are long-lived but only
their HMAC is stored (so a DB leak doesn't grant login).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey, String
from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

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
    token_hash: str = Field(
        sa_column=Column(String(128), nullable=False, unique=True),
    )
    expires_at: datetime = Field(nullable=False)
    revoked: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
