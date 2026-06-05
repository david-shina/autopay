"""Telegram link codes — short-lived codes users send to the bot to link.

Replaces the MVP's `TelegramLinkCode` which lived in the same file as User.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, ForeignKey, String
from sqlmodel import Field, SQLModel


class TelegramLinkCode(SQLModel, table=True):
    __tablename__ = "telegram_link_codes"

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
    code: str = Field(
        sa_column=Column(String(8), nullable=False, unique=True, index=True),
    )
    expires_at: datetime = Field(nullable=False)
    is_used: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)

    @staticmethod
    def generate_code() -> str:
        """6-char alphanumeric, uppercase, easy to read/type."""
        return secrets.token_hex(3).upper()
