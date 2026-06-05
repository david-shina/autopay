"""Integration test fixtures.

These tests hit a real database (`autopay_test`). We use a separate
DB from the dev one so the test can truncate every table between
tests without nuking seeded data.

DB setup is one-time:
  PGPASSWORD='...' psql -U postgres -h localhost -d postgres \\
    -c "CREATE DATABASE autopay_test;"
  PGPASSWORD='...' psql -U postgres -h localhost -d autopay_test \\
    -f schema.sql
"""
from __future__ import annotations

import os
from typing import Any, Callable

import pytest
from cryptography.fernet import Fernet

# ── Test env vars BEFORE any `from app...` import ───────────────────
TEST_DB_URL = "postgresql://postgres:David*2020*@localhost:5432/autopay_test"
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-must-be-at-least-32-chars-long"
os.environ["BVN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["PAYSTACK_SECRET_KEY"] = "sk_test_dummy"
# Force the regex fallback in the loaders (no real LLM calls). Some
# tests assert that the endpoint returns 422 when the LLM can't pull
# a vendor from the input. The hardcoded `groq_api_key` default in
# `app.core.config` is a real working key, so without this override
# those tests would silently switch to LLM-extracted values.
os.environ["GROQ_API_KEY"] = ""

# Now import the app
from app.core import config as _config  # noqa: E402
from app.core import database as _database  # noqa: E402
import app.core.config as _config_mod  # noqa: E402
import app.services.crypto as _crypto  # noqa: E402

_config.get_settings.cache_clear()
_crypto._fernet.cache_clear()
_database.engine.dispose()  # discard any old engine bound to the dev DB

# IMPORTANT: also refresh the module-level `settings` shortcut. If
# `app.core.config` was imported earlier (e.g. by tests/conftest.py),
# `settings` is still pointing at a stale Settings instance built
# BEFORE we set the env vars. Reassign it now.
_config_mod.settings = _config.get_settings()

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, text  # noqa: E402

from app import models  # noqa: E402,F401  (registers tables)
from app.core.database import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.payments.base import VirtualAccountData  # noqa: E402

# TABLES to truncate between tests. Order doesn't matter (CASCADE).
TABLES = [
    "audit_logs",
    "refresh_tokens",
    "transactions",
    "bills",
    "virtual_accounts",
    "kyc_records",
    "telegram_link_codes",
    "webhook_events",
    "users",
]


def _truncate_all() -> None:
    with engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(TABLES)
                + " RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _clean_db():
    """Truncate every table before each integration test."""
    _truncate_all()
    yield
    _truncate_all()


@pytest.fixture
def client() -> TestClient:
    """A TestClient with the test DB wired in."""
    return TestClient(app)


@pytest.fixture
def session() -> Session:
    """Direct DB session for tests that need to set up data without HTTP."""
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()


# ── Payment provider stub ──────────────────────────────────────────
# Used by every test that hits signup or a bill/payout flow. We don't
# want a real Paystack call; the stub records calls and returns canned
# responses.

class _StubPaystack:
    """Default stub: create customer + DVA, everything else raises."""

    name = "paystack"
    calls: list[tuple[str, dict]] = []
    _counter: int = 0

    async def create_customer(self, **kwargs):
        self.calls.append(("create_customer", kwargs))
        type(self)._counter += 1
        return f"CUS_test_{type(self)._counter}"

    async def create_virtual_account(self, **kwargs):
        self.calls.append(("create_virtual_account", kwargs))
        type(self)._counter += 1
        return VirtualAccountData(
            account_number=f"0123456789{type(self)._counter}",
            account_name=kwargs.get("customer_code", "Test User"),
            bank_name="GTBank",
            bank_code="058",
            provider_reference=f"42_{type(self)._counter}",
            provider="paystack",
        )

    async def resolve_account(self, **kwargs):
        self.calls.append(("resolve_account", kwargs))
        from app.services.payments.base import ResolvedAccount
        return ResolvedAccount(
            account_number=kwargs["account_number"],
            account_name="DSTV NG LTD",
            bank_code=kwargs["bank_code"],
        )

    async def create_transfer_recipient(self, **kwargs):
        self.calls.append(("create_transfer_recipient", kwargs))
        return "RCP_test"

    async def initiate_transfer(self, **kwargs):
        self.calls.append(("initiate_transfer", kwargs))
        from app.services.payments.base import TransferResult
        return TransferResult(
            provider_reference=kwargs["reference"],
            provider_transfer_id="99",
            status="pending",
        )

    def verify_webhook_signature(self, **_) -> bool:
        return True

    async def parse_webhook(self, **kwargs):
        from app.services.payments.base import WebhookEvent
        return WebhookEvent(
            event_type=kwargs.get("event_type", "charge.success"),
            provider_reference=kwargs.get("reference", "x"),
            event_id=kwargs.get("event_id", f"evt_{id(self)}"),
        )


@pytest.fixture
def stub_provider():
    """A fresh stub for each test. Overrides `get_payment_provider`."""
    from app.api import auth as auth_module
    from app.api import bills as bills_module

    _StubPaystack._counter = 0
    stub = _StubPaystack()
    stub.calls = []

    app.dependency_overrides[auth_module.get_payment_provider] = lambda: stub
    app.dependency_overrides[bills_module.get_payment_provider] = lambda: stub
    yield stub
    app.dependency_overrides.clear()

