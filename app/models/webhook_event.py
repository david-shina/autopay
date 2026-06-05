"""Webhook event dedup table.

Defends against Paystack retrying the same event on a network blip.
The provider's webhook contract: each event has a stable `id` (or
we can hash the body) and the same `id`/body may be delivered more
than once. We record the (provider, event_id) pair on first delivery
and short-circuit the second one with a 200.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, Index, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class WebhookEvent(SQLModel, table=True):
    __tablename__ = "webhook_events"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    provider: str = Field(sa_column=Column(String(32), nullable=False))
    event_id: str = Field(sa_column=Column(String(128), nullable=False))
    event_type: str = Field(sa_column=Column(String(64), nullable=False))
    received_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=False), nullable=False, index=True),
    )

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_events_provider_event_id"),
    )
