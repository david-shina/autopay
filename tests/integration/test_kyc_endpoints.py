"""Integration tests for the KYC endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.security import hash_password
from app.models.kyc import KycRecord
from app.models.user import User


def _auth_header(client: TestClient) -> str:
    s = client.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@x.com",
            "phone_number": "08031112233",
            "password": "Secret123",
        },
    )
    return f"Bearer {s.json()['access_token']}"


def test_submit_bvn_stores_encrypted(client: TestClient, session: Session) -> None:
    h = _auth_header(client)
    r = client.post("/api/v1/kyc/bvn", json={"bvn": "22123456789"}, headers={"Authorization": h})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["bvn_last4"] == "6789"
    assert body["bvn_validated"] is False

    # Inspect the DB: ciphertext should NOT contain the plaintext BVN
    rec = session.exec(select(KycRecord)).first()
    assert rec is not None
    assert b"22123456789" not in rec.bvn_ciphertext
    # And the hash is stored, not the BVN itself
    assert rec.bvn_hash != "22123456789"
    assert len(rec.bvn_hash) == 64  # SHA-256 hex


def test_submit_bvn_409_on_duplicate(client: TestClient) -> None:
    h = _auth_header(client)
    r1 = client.post("/api/v1/kyc/bvn", json={"bvn": "22123456789"}, headers={"Authorization": h})
    r2 = client.post("/api/v1/kyc/bvn", json={"bvn": "22123456789"}, headers={"Authorization": h})
    assert r1.status_code == 201
    assert r2.status_code == 409


def test_get_kyc_returns_status(client: TestClient) -> None:
    h = _auth_header(client)
    client.post("/api/v1/kyc/bvn", json={"bvn": "22123456789"}, headers={"Authorization": h})
    r = client.get("/api/v1/kyc/bvn", headers={"Authorization": h})
    assert r.status_code == 200
    body = r.json()
    assert body["bvn_last4"] == "6789"
    # Verify the response does NOT contain the full BVN
    assert "22123456789" not in str(body)


def test_get_kyc_404_when_absent(client: TestClient) -> None:
    h = _auth_header(client)
    r = client.get("/api/v1/kyc/bvn", headers={"Authorization": h})
    assert r.status_code == 404


def test_bvn_must_be_11_digits(client: TestClient) -> None:
    h = _auth_header(client)
    r = client.post("/api/v1/kyc/bvn", json={"bvn": "123"}, headers={"Authorization": h})
    assert r.status_code == 422
