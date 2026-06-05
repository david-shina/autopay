"""Password hashing + JWT helpers (bcrypt + python-jose).

These land in Chunk 2 (passwords) and Chunk 3 (JWT) but live in `core/`
because both are low-level primitives used by many modules.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


# ── Password hashing ────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt (cost factor 12)."""
    if not plain:
        raise ValueError("password must be non-empty")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt comparison. Returns False on any error."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── JWT helpers ──────────────────────────────────────────────────────

class JWTError_(Exception):  # noqa: N801
    """Raised when a JWT is invalid, expired, or tampered with."""


def _require_secret() -> str:
    if not settings.jwt_secret_key:
        raise JWTError_(
            "JWT_SECRET_KEY is not set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\"",
        )
    return settings.jwt_secret_key


def create_access_token(
    subject: str | int,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Issue a short-lived access token (default 15 min)."""
    secret = _require_secret()
    now = datetime.now(tz=timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.jwt_access_ttl_min))
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
) -> tuple[str, datetime]:
    """Issue a long-lived refresh token. Returns (token, expires_at).

    The token includes a random `jti` (JWT ID) so two refresh tokens
    for the same user issued in the same second are still uniquely
    identifiable in our `refresh_tokens` table.
    """
    secret = _require_secret()
    now = datetime.now(tz=timezone.utc)
    expire = now + (expires_delta or timedelta(days=settings.jwt_refresh_ttl_days))
    payload = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": secrets.token_urlsafe(16),
        "type": "refresh",
    }
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm), expire


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """Decode + verify a JWT. Raises JWTError_ on any failure."""
    secret = _require_secret()
    try:
        payload = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise JWTError_(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise JWTError_(f"expected token type {expected_type!r}, got {payload.get('type')!r}")
    return payload
