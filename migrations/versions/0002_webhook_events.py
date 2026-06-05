"""Add webhook_events table for replay defense.

Revision ID: 0002_webhook_events
Revises: 0001_baseline
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_webhook_events"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "provider", "event_id", name="uq_webhook_events_provider_event_id"
        ),
    )
    op.create_index(
        "idx_webhook_events_received_at",
        "webhook_events",
        ["received_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_webhook_events_received_at", table_name="webhook_events")
    op.drop_table("webhook_events")
