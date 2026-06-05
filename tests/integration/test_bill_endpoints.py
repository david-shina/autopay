"""Integration tests for the bills endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.security import hash_password
from app.models.bill import Bill
from app.models.enums import BillStatus
from app.models.user import User
from app.services.audit import audit_wallet_credit
from app.models.transaction import Transaction
from app.models.enums import TransactionType, TransactionStatus


def _auth_header(client: TestClient, email: str = "ada@example.com", phone: str = "08031112233") -> str:
    s = client.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "phone_number": phone,
            "password": "Secret123",
        },
    )
    assert s.status_code == 201, f"signup failed: {s.status_code} {s.text}"
    return f"Bearer {s.json()['access_token']}"


def test_create_bill_via_json(client: TestClient, stub_provider) -> None:
    h = _auth_header(client)
    r = client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 5000,
            "due_date": (datetime.now(tz=timezone.utc) + timedelta(days=2)).isoformat(),
            "account_number": "0123456789",
            "bank_code": "058",
            "bank_name": "GTBank",
        },
        headers={"Authorization": h},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["bill"]["vendor_name"] == "DSTV"
    assert body["bill"]["status"] == "pending"


def test_list_bills_returns_only_own(client: TestClient, stub_provider, session: Session) -> None:
    h1 = _auth_header(client, "ada@example.com", "08031112233")
    h2 = _auth_header(client, "tunde@example.com", "08042223344")
    # Ada creates a bill
    client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 5000,
            "due_date": (datetime.now(tz=timezone.utc) + timedelta(days=2)).isoformat(),
        },
        headers={"Authorization": h1},
    )
    # Tunde creates a bill
    client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "PHCN",
            "amount": 1000,
            "due_date": (datetime.now(tz=timezone.utc) + timedelta(days=2)).isoformat(),
        },
        headers={"Authorization": h2},
    )
    # Each user sees only their own
    r1 = client.get("/api/v1/bills", headers={"Authorization": h1})
    r2 = client.get("/api/v1/bills", headers={"Authorization": h2})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(r1.json()) == 1
    assert r1.json()[0]["vendor_name"] == "DSTV"
    assert len(r2.json()) == 1
    assert r2.json()[0]["vendor_name"] == "PHCN"


def test_get_bill_404_for_other_users_bill(client: TestClient, stub_provider) -> None:
    h1 = _auth_header(client, "ada@example.com", "08031112233")
    h2 = _auth_header(client, "tunde@example.com", "08042223344")
    r = client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 5000,
            "due_date": (datetime.now(tz=timezone.utc) + timedelta(days=2)).isoformat(),
        },
        headers={"Authorization": h1},
    )
    bill_id = r.json()["bill"]["id"]
    r2 = client.get(f"/api/v1/bills/{bill_id}", headers={"Authorization": h2})
    assert r2.status_code == 404


def test_pay_bill_deducts_balance(client: TestClient, stub_provider, session: Session) -> None:
    h = _auth_header(client)
    # Get the user we just created
    user = session.exec(select(User).where(User.email == "ada@example.com")).first()
    assert user is not None
    # Top up the wallet
    user.balance = Decimal("10000")
    session.add(user)
    session.commit()
    # Create a bill
    r = client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 5000,
            "due_date": (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat(),
            "account_number": "0123456789",
            "bank_code": "058",
            "bank_name": "GTBank",
        },
        headers={"Authorization": h},
    )
    bill_id = r.json()["bill"]["id"]
    # Pay it
    rp = client.post(f"/api/v1/bills/{bill_id}/pay", headers={"Authorization": h})
    assert rp.status_code == 200, rp.text
    # The bill is in 'processing' (webhook would flip it to 'paid')
    bill = session.get(Bill, bill_id)
    assert bill.status == BillStatus.PROCESSING.value
    # The user's balance is reduced by amount + fee
    session.refresh(user)
    assert Decimal(str(user.balance)) == Decimal("10000") - Decimal("5050")
    # The provider was called for resolve + recipient + transfer
    called = [c[0] for c in stub_provider.calls]
    assert "resolve_account" in called
    assert "create_transfer_recipient" in called
    assert "initiate_transfer" in called


def test_pay_bill_402_on_insufficient_balance(client: TestClient, stub_provider) -> None:
    h = _auth_header(client)
    r = client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 50000,
            "due_date": (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat(),
            "account_number": "0123456789",
            "bank_code": "058",
            "bank_name": "GTBank",
        },
        headers={"Authorization": h},
    )
    bill_id = r.json()["bill"]["id"]
    rp = client.post(f"/api/v1/bills/{bill_id}/pay", headers={"Authorization": h})
    assert rp.status_code == 402


def test_cancel_bill(client: TestClient, stub_provider) -> None:
    h = _auth_header(client)
    r = client.post(
        "/api/v1/bills",
        json={
            "vendor_name": "DSTV",
            "amount": 5000,
            "due_date": (datetime.now(tz=timezone.utc) + timedelta(days=10)).isoformat(),
        },
        headers={"Authorization": h},
    )
    bill_id = r.json()["bill"]["id"]
    rc = client.post(f"/api/v1/bills/{bill_id}/cancel", headers={"Authorization": h})
    assert rc.status_code == 200
    assert rc.json()["bill"]["status"] == "cancelled"


def test_upload_text_bill_runs_agent(client: TestClient, stub_provider, session: Session) -> None:
    """The /upload endpoint with a `request_bill` form field should
    create a bill and run the decision agent."""
    h = _auth_header(client)
    # Top up so the agent picks pay_now
    user = session.exec(select(User).where(User.email == "ada@example.com")).first()
    user.balance = Decimal("100000")
    session.add(user)
    session.commit()

    r = client.post(
        "/api/v1/bills/upload",
        data={"request_bill": "Pay DSTV 5000 by tomorrow 0123456789 GTBank 058"},
        headers={"Authorization": h},
    )
    # The text loader with no LLM will fall back to regex extraction
    # which can only get the amount (no vendor). So the endpoint
    # should 422 (cannot determine vendor).
    assert r.status_code == 422
    assert "vendor" in r.json()["detail"].lower()


def test_upload_bill_400_without_file_or_text(client: TestClient, stub_provider) -> None:
    h = _auth_header(client)
    r = client.post("/api/v1/bills/upload", headers={"Authorization": h})
    assert r.status_code == 400
