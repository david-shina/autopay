"""Integration tests for the wallet endpoints (DVA provisioning)."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.virtual_account import VirtualAccount


def _signup(client: TestClient, email: str = "wallet@x.com", phone: str = "08090000001") -> str:
    s = client.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Wallet",
            "last_name": "Tester",
            "email": email,
            "phone_number": phone,
            "password": "Secret123",
        },
    )
    assert s.status_code == 201, s.text
    return f"Bearer {s.json()['access_token']}"


def test_provision_virtual_account_creates_dva(
    client: TestClient, stub_provider, session: Session
) -> None:
    h = _signup(client)
    r = client.post("/api/v1/wallet/provision", headers={"Authorization": h})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["already_existed"] is False
    assert body["virtual_account"]["account_number"]
    assert body["virtual_account"]["provider"] == "paystack"
    # Stub was called for customer + DVA creation
    called = [c[0] for c in stub_provider.calls]
    assert "create_customer" in called
    assert "create_virtual_account" in called
    # DB row exists
    va = session.exec(select(VirtualAccount)).first()
    assert va is not None
    assert va.account_number == body["virtual_account"]["account_number"]


def test_provision_virtual_account_idempotent(
    client: TestClient, stub_provider
) -> None:
    h = _signup(client, email="idem@x.com", phone="08090000002")
    r1 = client.post("/api/v1/wallet/provision", headers={"Authorization": h})
    assert r1.status_code == 201
    first_body = r1.json()
    # second call should NOT hit the provider
    calls_after_first = len(stub_provider.calls)
    r2 = client.post("/api/v1/wallet/provision", headers={"Authorization": h})
    assert r2.status_code == 201
    second_body = r2.json()
    assert second_body["already_existed"] is True
    assert second_body["virtual_account"]["account_number"] == first_body["virtual_account"]["account_number"]
    assert len(stub_provider.calls) == calls_after_first


def test_provision_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/wallet/provision")
    assert r.status_code == 401


# ── Telegram link-code ─────────────────────────────────────────────


def test_create_telegram_link_code(client: TestClient) -> None:
    h = _signup(client, email="tgcode@x.com", phone="08090000003")
    r = client.post("/api/v1/auth/telegram/link-code", headers={"Authorization": h})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["code"]) == 6
    assert "expires_at" in body


def test_create_telegram_link_code_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/auth/telegram/link-code")
    assert r.status_code == 401


def test_unlink_telegram_clears_chat_id(
    client: TestClient, session: Session
) -> None:
    from app.core.security import hash_password
    from app.models.user import User
    from app.core.database import session_scope
    h = _signup(client, email="ulnk@x.com", phone="08090000004")
    with session_scope() as s:
        u = s.exec(select(User).where(User.email == "ulnk@x.com")).first()
        u.telegram_chat_id = "12345"
        u.is_telegram_linked = True
        s.add(u)

    r = client.delete("/api/v1/auth/telegram/link", headers={"Authorization": h})
    assert r.status_code == 204

    with session_scope() as s:
        u = s.exec(select(User).where(User.email == "ulnk@x.com")).first()
        assert u.telegram_chat_id is None
        assert u.is_telegram_linked is False
