"""Conftest for unit tests that need DB access.

The DB-backed unit tests in `test_telegram_handlers.py` reuse the
integration conftest's `session` fixture (Postgres + JSONB) so we
don't have to maintain a separate SQLite schema. This conftest
exists only to make the integration fixtures visible from the
`tests/unit/` directory.
"""
from tests.integration.conftest import (  # noqa: F401
    _clean_db,
    _truncate_all,
    client,
    session,
    stub_provider,
)
