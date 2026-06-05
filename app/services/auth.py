"""Auth service — signup, login, token issuance, FastAPI dependencies.

The shape of the API:
  * `signup_user`       — create user (with hashed password) + KYC placeholder + DVA, write audit
  * `authenticate_user` — verify email + password
  * `issue_tokens`      — access (JWT) + refresh (JWT + DB row of hash)
  * `rotate_tokens`     — exchange a valid refresh for a new pair (old one revoked)
  * `revoke_refresh`    — invalidate a refresh token
  * `get_current_user`  — FastAPI dependency; decodes the access token, loads the user

Refresh tokens are JWTs *and* have their hash stored. Why both?
  * JWT = stateless, FastAPI can decode without hitting the DB on every call.
  * DB hash = server-side revocation ("log out everywhere"), rotation detection.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.core.security import (
    JWTError_,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.enums import AuditActor
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.audit import audit_login, audit_logout, audit_user_signup


# ── FastAPI security scheme ──────────────────────────────────────────

# Declared at module scope so FastAPI's OpenAPI generator picks it up
# and emits `securitySchemes.BearerAuth` in /openapi.json. That, in turn,
# makes Swagger UI's "Authorize" button appear and the generated curl
# from "Try it out" include the `Authorization: Bearer <token>` header.
#
# auto_error=False is critical: with True, FastAPI returns its own
# `403 "Not authenticated"` before our code runs, losing the
# `WWW-Authenticate: Bearer` response header and our custom message.
bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    scheme_name="BearerAuth",
)


# ── Refresh-token storage helpers ───────────────────────────────────

def _hash_refresh_token(token: str) -> str:
    """SHA-256 the refresh token. Refresh tokens are high-entropy JWTs,
    not user-chosen passwords, so a single SHA-256 is sufficient and
    cheap. The hash is the unique key in the `refresh_tokens` table."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _store_refresh_token(
    session: Session, *, user_id: int, token: str, expires_at: datetime
) -> RefreshToken:
    row = RefreshToken(
        user_id=user_id,
        token_hash=_hash_refresh_token(token),
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


# ── Signup ──────────────────────────────────────────────────────────

def signup_user(
    session: Session,
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    phone_number: str,
    ip_address: Optional[str] = None,
) -> User:
    """Create a new user. Raises 409 on duplicate email/phone.

    Password is bcrypt-hashed. KYC placeholder is *not* created here —
    the user submits BVN separately (so we never accidentally store
    plaintext BVN).
    """
    user = User(
        email=email.lower().strip(),
        hashed_password=hash_password(password),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone_number=phone_number.strip(),
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email or phone already exists.",
        ) from exc

    audit_user_signup(session, user_id=user.id, ip=ip_address)
    return user


# ── Authenticate + issue tokens ─────────────────────────────────────

def authenticate_user(session: Session, *, email: str, password: str) -> User:
    """Return the user if email+password match. Raises 401 otherwise."""
    user = session.exec(select(User).where(User.email == email.lower().strip())).first()
    if not user or not verify_password(password, user.hashed_password):
        # Run bcrypt verify against a dummy hash anyway to make timing
        # constant with the success path.
        verify_password(password, "$2b$12$" + "x" * 53)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    return user


def issue_tokens(
    session: Session,
    *,
    user: User,
    audit: bool = True,
    ip: Optional[str] = None,
) -> tuple[str, str, int]:
    """Mint an access+refresh pair. Stores the refresh token hash.

    Returns (access_token, refresh_token, expires_in_seconds).
    """
    access = create_access_token(subject=user.id)
    refresh, refresh_expires = create_refresh_token(subject=user.id)
    _store_refresh_token(
        session, user_id=user.id, token=refresh, expires_at=refresh_expires
    )
    if audit:
        audit_login(session, user_id=user.id, ip=ip)
    session.commit()
    return access, refresh, settings.jwt_access_ttl_min * 60


def rotate_tokens(
    session: Session,
    *,
    refresh_token: str,
    ip: Optional[str] = None,
) -> tuple[str, str, int]:
    """Exchange a valid refresh token for a new pair. The old refresh
    is revoked atomically (revoke + insert in one transaction)."""
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except JWTError_ as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid refresh token: {exc}",
        ) from exc

    user_id = int(payload["sub"])
    incoming_hash = _hash_refresh_token(refresh_token)

    # Atomically look up + revoke the incoming refresh. If it's already
    # revoked, this is a replay — refuse the rotation.
    row = session.exec(
        select(RefreshToken).where(
            RefreshToken.token_hash == incoming_hash,
            RefreshToken.user_id == user_id,
        )
    ).first()
    if row is None or row.revoked or row.expires_at < datetime.now(tz=timezone.utc):
        # Treat reuse of a revoked/expired token as compromise: revoke
        # all of this user's refresh tokens.
        _revoke_all_for_user(session, user_id=user_id)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or expired. Please log in again.",
        )
    row.revoked = True
    session.flush()

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists.",
        )

    access = create_access_token(subject=user.id)
    new_refresh, new_expires = create_refresh_token(subject=user.id)
    _store_refresh_token(
        session, user_id=user.id, token=new_refresh, expires_at=new_expires
    )
    audit_login(session, user_id=user.id, ip=ip)  # re-auth event
    session.commit()
    return access, new_refresh, settings.jwt_access_ttl_min * 60


def revoke_refresh(session: Session, *, refresh_token: str) -> None:
    """Mark a refresh token as revoked. Idempotent."""
    incoming_hash = _hash_refresh_token(refresh_token)
    row = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == incoming_hash)
    ).first()
    if row is not None and not row.revoked:
        row.revoked = True
        session.commit()


def _revoke_all_for_user(session: Session, *, user_id: int) -> None:
    """Revoke every outstanding refresh token for `user_id`."""
    rows = session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked == False  # noqa: E712
        )
    ).all()
    for r in rows:
        r.revoked = True
    session.flush()


def logout_user(
    session: Session,
    *,
    user: User,
    refresh_token: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    """Log the user out. If `refresh_token` is given, revoke *that*
    specific one; otherwise revoke all of the user's outstanding
    refresh tokens (full sign-out-everywhere)."""
    if refresh_token:
        revoke_refresh(session, refresh_token=refresh_token)
    else:
        _revoke_all_for_user(session, user_id=user.id)
        session.commit()
    audit_logout(session, user_id=user.id, ip=ip)
    session.commit()


# ── FastAPI dependencies ────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    """FastAPI dependency: decodes the access token, loads the user.

    Declared via the `bearer_scheme` HTTPBearer so FastAPI emits the
    `securitySchemes` OpenAPI block and Swagger UI's "Authorize" button
    is wired up. With `auto_error=False`, FastAPI does *not* raise its
    own 403 when the header is absent — we raise our own 401 with the
    `WWW-Authenticate: Bearer` response header.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = decode_token(token, expected_type="access")
    except JWTError_ as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user_id = int(payload["sub"])
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_active_user(
    user: User = Depends(get_current_user),
) -> User:
    """Optional 'is_active' check goes here. We don't have such a flag
    on the model yet, so this is just `get_current_user` for now."""
    return user


def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> Optional[User]:
    """Like `get_current_user` but returns None when the token is
    missing/invalid. Useful for endpoints that are public but
    personalize the response when authed (e.g. health)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    try:
        return get_current_user(credentials=credentials, session=session)
    except HTTPException:
        return None


# ── Misc utility (kept here so routers don't need it) ───────────────

def new_refresh_token_string() -> str:
    """Random opaque string for opaque-token flows. We use JWTs, but
    this exists for callers that want a non-JWT opaque token."""
    return secrets.token_urlsafe(48)
