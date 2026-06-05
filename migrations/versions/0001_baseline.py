"""Baseline migration.

The initial schema is defined in `../schema.sql` (loaded directly into Postgres
on first boot via /docker-entrypoint-initdb.d, or via `make db-init` for local
dev). This migration exists as a version marker so that:

  1. `alembic stamp head` on an existing DB marks it as up-to-date.
  2. `alembic upgrade head` on a fresh DB is a no-op (tables already exist
     via schema.sql's CREATE TABLE IF NOT EXISTS statements run by the
     postgres container's entrypoint).
  3. Future migrations (add column, drop index, etc.) build on top of this
     baseline version.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op. See module docstring."""
    pass


def downgrade() -> None:
    """No-op. To wipe the schema, drop and recreate the database."""
    pass
