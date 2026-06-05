"""Integration tests for the auth endpoints.

Coverage:
  * signup  → 201, tokens returned
  * signup  → 409 on duplicate email
  * login   → 200, tokens
  * login   → 401 on bad password
  * refresh → 200, new tokens
  * refresh → 401 on revoked/expired
  * logout  → 204; subsequent refresh fails
  * me      → 200 with authed user info
  * me      → 401 without token
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.security import hash_password
from app.models.user import User


def _signup_payload() -> dict:
    return {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "phone_number": "08031112233",
        "password": "Secret123",
    }


def test_signup_returns_tokens(client: TestClient, stub_provider) -> None:
    resp = client.post("/api/v1/auth/signup", json=_signup_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    # Signup is non-blocking for DVA by default. The provider is NOT
    # called; the user calls POST /api/v1/wallet/provision later (see
    # `test_provision_virtual_account_idempotent`).
    assert not any(c[0] == "create_customer" for c in stub_provider.calls)


def test_signup_409_on_duplicate(client: TestClient, stub_provider) -> None:
    r1 = client.post("/api/v1/auth/signup", json=_signup_payload())
    r2 = client.post("/api/v1/auth/signup", json=_signup_payload())
    assert r1.status_code == 201
    assert r2.status_code == 409


def test_login_happy_path(client: TestClient, stub_provider) -> None:
    client.post("/api/v1/auth/signup", json=_signup_payload())
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": _signup_payload()["email"], "password": _signup_payload()["password"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_401_on_bad_password(client: TestClient, session: Session) -> None:
    session.add(
        User(
            email="ada@example.com",
            hashed_password=hash_password("Secret123"),
            first_name="Ada",
            last_name="Lovelace",
            phone_number="0801",
        )
    )
    session.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "WRONG"},
    )
    assert resp.status_code == 401


def test_refresh_returns_new_tokens(client: TestClient, stub_provider) -> None:
    s = client.post("/api/v1/auth/signup", json=_signup_payload())
    refresh = s.json()["refresh_token"]
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    new_refresh = resp.json()["refresh_token"]
    assert new_refresh != refresh


def test_refresh_401_after_revoke(client: TestClient, stub_provider) -> None:
    s = client.post("/api/v1/auth/signup", json=_signup_payload())
    refresh = s.json()["refresh_token"]
    # First refresh works
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r1.status_code == 200
    # Replaying the OLD refresh must fail (replay-attack defense)
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401


def test_logout_revokes_refresh(client: TestClient, stub_provider) -> None:
    s = client.post("/api/v1/auth/signup", json=_signup_payload())
    access = s.json()["access_token"]
    refresh = s.json()["refresh_token"]
    r = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401


def test_me_returns_user_info(client: TestClient, stub_provider) -> None:
    s = client.post("/api/v1/auth/signup", json=_signup_payload())
    access = s.json()["access_token"]
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == _signup_payload()["email"]
    assert "hashed_password" not in body


def test_me_401_without_token(client: TestClient) -> None:
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_wallet_endpoint_returns_balance(client: TestClient, stub_provider) -> None:
    s = client.post("/api/v1/auth/signup", json=_signup_payload())
    access = s.json()["access_token"]
    r = client.get("/api/v1/auth/wallet", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    body = r.json()
    assert body["balance"] == 0.0
    assert body["currency"] == "NGN"


def test_openapi_declares_bearer_security_scheme(client: TestClient) -> None:
    """Swagger UI's 'Authorize' button + the curl 'Authorization' header
    in 'Try it out' both depend on FastAPI emitting a `securitySchemes`
    block. Catches future refactors that accidentally drop the
    `bearer_scheme` dep and leave Swagger without a way to send the
    Bearer token."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schemes = r.json()["components"]["securitySchemes"]
    assert "BearerAuth" in schemes
    assert schemes["BearerAuth"]["type"] == "http"
    assert schemes["BearerAuth"]["scheme"] == "bearer"
    assert schemes["BearerAuth"]["bearerFormat"] == "JWT"
    # The protected /me endpoint must reference the scheme.
    me_security = r.json()["paths"]["/api/v1/auth/me"]["get"].get("security", [])
    assert me_security == [{"BearerAuth": []}]
    # Public endpoints (signup, login, refresh) must NOT require it.
    for path in ("/api/v1/auth/signup", "/api/v1/auth/login", "/api/v1/auth/refresh"):
        sec = r.json()["paths"][path].get("post", {}).get("security", [])
        assert sec == [], f"{path} should be public, got security={sec}"


def test_401_includes_www_authenticate_header(client: TestClient) -> None:
    """RFC 6750 says a 401 on a Bearer-protected route must include
    `WWW-Authenticate: Bearer` so clients know how to retry."""
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")
