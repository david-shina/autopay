"""Tests for the audit log writer.

Uses an in-memory SQLite engine to verify the writer behaves correctly
without needing a real Postgres connection. The audit_log model uses
JSONB + INET types from Postgres, so we test the *Python-side* behaviour
(write_audit adds a row, session.flush populates id, etc.) by patching
the session to use a simpler table or by checking the constructed object.

Since AuditLog uses Postgres-specific column types, we test the helper
function in isolation rather than round-tripping through the DB here.
Full integration tests land in Chunk 3.
"""
from __future__ import annotations

from app.models.enums import AuditActor, AuditEventType
from app.services.audit import (
    audit_login,
    audit_payout_failed,
    audit_payout_succeeded,
    audit_user_signup,
    audit_wallet_credit,
    audit_wallet_debit,
    write_audit,
)


class FakeSession:
    """Just enough of a Session to verify what write_audit() did."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        # Simulate the DB populating the primary key
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 1


def test_write_audit_appends_row() -> None:
    session = FakeSession()
    row = write_audit(
        session,
        actor=AuditActor.SYSTEM,
        event_type=AuditEventType.BILL_CREATED,
        user_id=42,
        entity_type="bill",
        entity_id=7,
        before_state={"status": "pending"},
        after_state={"status": "scheduled"},
        metadata={"reason": "user approved"},
    )
    assert row in session.added
    assert row.id == 1
    assert row.user_id == 42
    assert row.event_type == "bill.created"  # enum coerced to value
    assert row.before_state == {"status": "pending"}
    assert row.event_metadata == {"reason": "user approved"}


def test_write_audit_accepts_plain_strings() -> None:
    session = FakeSession()
    row = write_audit(
        session,
        actor="user",
        event_type="custom.event",
        user_id=1,
    )
    assert row.actor == "user"
    assert row.event_type == "custom.event"


def test_audit_user_signup_helper() -> None:
    session = FakeSession()
    row = audit_user_signup(session, user_id=99, ip="10.0.0.1")
    assert row.event_type == AuditEventType.USER_SIGNUP.value
    assert row.actor == AuditActor.USER.value
    assert row.entity_type == "user"
    assert row.entity_id == 99
    assert row.ip_address == "10.0.0.1"


def test_audit_login_helper() -> None:
    session = FakeSession()
    row = audit_login(session, user_id=5, ip="192.168.1.1")
    assert row.event_type == AuditEventType.USER_LOGIN.value


def test_audit_wallet_credit_helper() -> None:
    session = FakeSession()
    row = audit_wallet_credit(
        session,
        user_id=5,
        amount=1000.0,
        provider_reference="ref_123",
        new_balance=5000.0,
    )
    assert row.event_type == AuditEventType.WALLET_CREDITED.value
    assert row.actor == AuditActor.WEBHOOK.value
    assert row.after_state == {"amount": 1000.0, "balance": 5000.0}
    assert row.event_metadata == {"provider_reference": "ref_123"}


def test_audit_wallet_debit_helper() -> None:
    session = FakeSession()
    row = audit_wallet_debit(
        session,
        user_id=5,
        amount=9000.0,
        fee=50.0,
        bill_id=42,
        provider_reference="ref_456",
        new_balance=4000.0,
    )
    assert row.event_type == AuditEventType.PAYOUT_ATTEMPTED.value
    assert row.entity_id == 42
    assert row.event_metadata is None  # money lives in after_state


def test_audit_payout_succeeded_helper() -> None:
    session = FakeSession()
    row = audit_payout_succeeded(
        session, user_id=5, bill_id=42, provider_reference="ref_789",
    )
    assert row.event_type == AuditEventType.PAYOUT_SUCCEEDED.value
    assert row.actor == AuditActor.WEBHOOK.value


def test_audit_payout_failed_helper() -> None:
    session = FakeSession()
    row = audit_payout_failed(
        session, user_id=5, bill_id=42, reason="insufficient funds", retry_count=1,
    )
    assert row.event_type == AuditEventType.PAYOUT_FAILED.value
    assert row.event_metadata == {"reason": "insufficient funds", "retry_count": 1}
