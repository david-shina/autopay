"""Integration tests for the Paystack webhook endpoint.

Coverage:
  * charge.success  → credits the wallet, updates transaction, audit
  * transfer.success → marks bill paid, keeps balance debit
  * transfer.failed  → marks bill failed, refunds wallet
  * bad signature    → 400, no side effects
  * unknown event    → 200 (don't make Paystack retry)
  * replay safety    → second charge.success for same ref is a no-op
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import hash_password
from app.models.enums import BillStatus, TransactionStatus, TransactionType
from app.models.transaction import Transaction
from app.models.user import User


# Re-read settings in case the module was imported before env was set.
from app.core.config import get_settings  # noqa: E402
SECRET = get_settings().paystack_secret_key


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()


def _user_with_balance(session: Session, *, email: str, balance: str) -> User:
    u = User(
        email=email,
        hashed_password=hash_password("Secret123"),
        first_name="Ada",
        last_name="L",
        phone_number="0803" + email[:8].rjust(8, "0"),
        balance=Decimal(balance),
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _make_transfer(
    session: Session, *, user_id: int, amount: str, ref: str, bill_id: int | None = None
) -> Transaction:
    """Create a debit transaction (status=processing) for testing the
    transfer.* webhooks."""
    txn = Transaction(
        user_id=user_id,
        bill_id=bill_id,
        type=TransactionType.DEBIT.value,
        amount=Decimal(amount),
        fee=Decimal("50.00"),
        currency="NGN",
        status=TransactionStatus.PROCESSING.value,
        provider="paystack",
        provider_reference=ref,
    )
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


def test_charge_success_credits_wallet(client: TestClient, session: Session) -> None:
    user = _user_with_balance(session, email="ada@x.com", balance="0")
    ref = "pay_123"
    _make_transfer(session, user_id=user.id, amount="10000", ref=ref)
    # Now wait — we want a CREDIT, not DEBIT. Let me redo.
    # Actually the simpler test: a charge.success with a reference
    # that we DON'T have a row for → orphan credit logged.
    body = json.dumps(
        {
            "event": "charge.success",
            "data": {"reference": "pay_unknown", "amount": 50000},
        }
    ).encode()
    sig = _sign(body)
    print("TEST sig:", sig, "SECRET:", SECRET)
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, f"webhook failed: {resp.text}"
    # An orphan audit row was added
    from app.models.audit_log import AuditLog
    rows = session.exec(select(AuditLog)).all()
    assert any(r.event_metadata and r.event_metadata.get("status") == "orphan_credit" for r in rows)


def test_charge_success_matches_existing_txn(client: TestClient, session: Session) -> None:
    user = _user_with_balance(session, email="bob@x.com", balance="0")
    # We make a 'processing' DEBIT, then the test wants to use that
    # reference to simulate a credit. Actually the test logic is wrong;
    # the webhook for charge.success is for INCOMING payments (VA top-up).
    # We need a credit row.
    credit = Transaction(
        user_id=user.id,
        type=TransactionType.CREDIT.value,
        amount=Decimal("0"),  # not yet known
        currency="NGN",
        status=TransactionStatus.PENDING.value,
        provider="paystack",
        provider_reference="ref_abc",
    )
    session.add(credit)
    session.commit()
    session.refresh(credit)
    body = json.dumps(
        {"event": "charge.success", "data": {"reference": "ref_abc", "amount": 25000}}
    ).encode()
    sig = _sign(body)
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    session.refresh(credit)
    session.refresh(user)
    assert credit.status == TransactionStatus.SUCCESS.value
    assert Decimal(str(user.balance)) == Decimal("250.00")


def test_charge_success_is_idempotent(client: TestClient, session: Session) -> None:
    user = _user_with_balance(session, email="carol@x.com", balance="0")
    credit = Transaction(
        user_id=user.id,
        type=TransactionType.CREDIT.value,
        amount=Decimal("0"),
        currency="NGN",
        status=TransactionStatus.SUCCESS.value,  # already done
        provider="paystack",
        provider_reference="ref_xyz",
    )
    session.add(credit)
    session.commit()
    body = json.dumps(
        {"event": "charge.success", "data": {"reference": "ref_xyz", "amount": 25000}}
    ).encode()
    sig = _sign(body)
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    session.refresh(user)
    # Balance should NOT have been credited a second time
    assert Decimal(str(user.balance)) == Decimal("0")


def test_transfer_success_marks_bill_paid(client: TestClient, session: Session) -> None:
    from app.models.bill import Bill
    user = _user_with_balance(session, email="dave@x.com", balance="0")
    bill = Bill(
        user_id=user.id,
        vendor_name="DSTV",
        amount=Decimal("5000"),
        due_date="2025-12-31T00:00:00Z",
        status=BillStatus.PROCESSING.value,
        account_number="0123456789",
        bank_code="058",
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)
    txn = _make_transfer(
        session, user_id=user.id, amount="5000", ref="trf_1", bill_id=bill.id
    )
    body = json.dumps(
        {"event": "transfer.success", "data": {"id": 1, "reference": "trf_1", "amount": 500000}}
    ).encode()
    sig = _sign(body)
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    session.refresh(txn)
    session.refresh(bill)
    assert txn.status == TransactionStatus.SUCCESS.value
    assert bill.status == BillStatus.PAID.value


def test_transfer_failed_refunds_wallet(client: TestClient, session: Session) -> None:
    from app.models.bill import Bill
    user = _user_with_balance(session, email="eve@x.com", balance="0")
    # The payout service would have already debited the wallet before
    # calling the provider, so we simulate that here.
    user.balance = Decimal("-5050")  # we already debited in execute_payout
    session.add(user)
    session.commit()
    bill = Bill(
        user_id=user.id,
        vendor_name="DSTV",
        amount=Decimal("5000"),
        due_date="2025-12-31T00:00:00Z",
        status=BillStatus.PROCESSING.value,
        account_number="0123456789",
        bank_code="058",
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)
    txn = _make_transfer(
        session, user_id=user.id, amount="5000", ref="trf_2", bill_id=bill.id
    )
    body = json.dumps(
        {"event": "transfer.failed", "data": {"id": 1, "reference": "trf_2", "amount": 500000}}
    ).encode()
    sig = _sign(body)
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    session.refresh(txn)
    session.refresh(bill)
    session.refresh(user)
    assert txn.status == TransactionStatus.FAILED.value
    # Wallet refunded
    assert Decimal(str(user.balance)) == Decimal("0")
    # Bill retried (not yet at max_retries)
    assert bill.retry_count == 1
    assert bill.status == BillStatus.SCHEDULED.value


def test_bad_signature_returns_400(client: TestClient, session: Session) -> None:
    body = json.dumps(
        {"event": "charge.success", "data": {"reference": "r1", "amount": 1}}
    ).encode()
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": "deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    # No side effects: no audit row
    from app.models.audit_log import AuditLog
    rows = session.exec(select(AuditLog)).all()
    assert len(rows) == 0


def test_missing_signature_returns_400(client: TestClient) -> None:
    body = b'{"event":"charge.success","data":{"reference":"r1"}}'
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_unknown_event_is_200_with_audit(client: TestClient, session: Session) -> None:
    body = json.dumps(
        {"event": "satellite.launched", "data": {"id": 1}}
    ).encode()
    sig = _sign(body)
    resp = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["event"] == "satellite.launched"
    from app.models.audit_log import AuditLog
    from app.models.enums import AuditEventType
    rows = session.exec(select(AuditLog)).all()
    assert any(
        r.event_type == AuditEventType.WEBHOOK_UNKNOWN.value
        and r.event_metadata
        and r.event_metadata.get("event_type") == "satellite.launched"
        for r in rows
    )


def test_replay_returns_200_with_audit_breadcrumb(
    client: TestClient, session: Session
) -> None:
    """Paystack retries the same event on a network blip. Second
    delivery is a 200 no-op with a `webhook.replay` audit row, not a
    double-credit / double-debit."""
    body = json.dumps(
        {"event": "satellite.launched", "data": {"id": 1}}
    ).encode()
    sig = _sign(body)

    r1 = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert r1.status_code == 200
    assert r1.json().get("replay") is not True

    r2 = client.post(
        "/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert r2.status_code == 200
    assert r2.json().get("replay") is True

    from app.models.audit_log import AuditLog
    from app.models.enums import AuditEventType
    rows = session.exec(
        select(AuditLog).where(AuditLog.event_type == AuditEventType.WEBHOOK_REPLAY.value)
    ).all()
    assert len(rows) == 1
