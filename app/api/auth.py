"""Auth API — signup, login, refresh, logout, me.

Mounted at /api/v1/auth in `app.main`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.core.http import client_ip as _client_ip
from app.models.telegram_link_code import TelegramLinkCode
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UserPublic,
    WalletBalance,
)
from app.services.auth import (
    authenticate_user,
    get_current_active_user,
    issue_tokens,
    logout_user,
    rotate_tokens,
    signup_user,
)
from app.services.payments import PaymentProvider, get_payment_provider
from app.services.audit import audit_va_created
from app.models.virtual_account import VirtualAccount

router = APIRouter(tags=["auth"])


# ── POST /auth/signup ───────────────────────────────────────────────

@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def signup(
    payload: SignupRequest,
    request: Request,
    session: Session = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> TokenResponse:
    """Create a user, issue tokens. DVA provisioning is best-effort
    and gated by `settings.auto_provision_dva_on_signup`:

      * `True`  → signup calls the payment provider synchronously and
                  creates the DVA inline. Any provider error is caught
                  and audit-logged; signup still returns 201 because
                  the user row is committed first.
      * `False` → signup just creates the user. The user (or admin)
                  calls `POST /api/v1/wallet/provision` to create the
                  DVA later, once the Paystack business is approved
                  for Dedicated NUBANs.

    `False` is the default because the Paystack Dedicated NUBAN
    feature requires business approval; turning it on prematurely
    causes every signup to silently fail-audit the DVA step.
    """
    user = signup_user(
        session,
        email=payload.email,
        password=payload.password,
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone_number=payload.phone_number,
        ip_address=_client_ip(request),
    )
    session.commit()  # user is durable; DVA failure below won't undo this
    session.refresh(user)

    if settings.auto_provision_dva_on_signup:
        await _try_provision_dva(
            session,
            user=user,
            provider=provider,
        )
    session.commit()

    access, refresh, expires_in = issue_tokens(
        session, user=user, ip=_client_ip(request)
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
    )


async def _try_provision_dva(
    session: Session,
    *,
    user,
    provider: PaymentProvider,
) -> None:
    """Best-effort DVA provisioning. On any provider error, writes a
    `va.created` audit row with `status: failed` and returns — the
    caller's transaction continues. Idempotent: a second call after
    a previous success is a no-op."""
    # Idempotency: if a VA already exists, return.
    from sqlmodel import select
    existing = session.exec(
        select(VirtualAccount).where(VirtualAccount.user_id == user.id)
    ).first()
    if existing is not None:
        return

    try:
        customer_code = await provider.create_customer(
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            phone=user.phone_number,
        )
        va_data = await provider.create_virtual_account(customer_code=customer_code)
    except Exception as exc:  # noqa: BLE001
        from app.services.audit import write_audit
        from app.models.enums import AuditActor, AuditEventType, AuditEntityType
        write_audit(
            session,
            actor=AuditActor.SYSTEM,
            event_type=AuditEventType.VA_CREATED,
            user_id=user.id,
            entity_type=AuditEntityType.USER,
            entity_id=user.id,
            metadata={
                "provider": provider.name,
                "error": str(exc),
                "status": "failed",
                "trigger": "signup",
            },
        )
        return

    va = VirtualAccount(
        user_id=user.id,
        provider=provider.name,
        provider_account_reference=va_data.provider_reference,
        account_number=va_data.account_number,
        account_name=va_data.account_name,
        bank_name=va_data.bank_name,
    )
    session.add(va)
    session.flush()
    audit_va_created(
        session,
        user_id=user.id,
        va_id=va.id or 0,
        provider=provider.name,
        account_number=va_data.account_number,
    )


# ── POST /auth/login ────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> TokenResponse:
    user = authenticate_user(session, email=payload.email, password=payload.password)
    access, refresh, expires_in = issue_tokens(
        session, user=user, ip=_client_ip(request)
    )
    return TokenResponse(
        access_token=access, refresh_token=refresh, expires_in=expires_in
    )


# ── POST /auth/refresh ──────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> TokenResponse:
    access, refresh_token, expires_in = rotate_tokens(
        session, refresh_token=payload.refresh_token, ip=_client_ip(request)
    )
    return TokenResponse(
        access_token=access, refresh_token=refresh_token, expires_in=expires_in
    )


# ── POST /auth/logout ───────────────────────────────────────────────

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(
    payload: LogoutRequest,
    request: Request,
    session: Session = Depends(get_session),
    user=Depends(get_current_active_user),
) -> Response:
    logout_user(
        session,
        user=user,
        refresh_token=payload.refresh_token,
        ip=_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── GET /auth/me ────────────────────────────────────────────────────

@router.get("/me", response_model=UserPublic)
def me(user=Depends(get_current_active_user)) -> UserPublic:
    return UserPublic.model_validate(user)


# ── GET /auth/wallet ────────────────────────────────────────────────

@router.get("/wallet", response_model=WalletBalance)
def wallet(user=Depends(get_current_active_user)) -> WalletBalance:
    return WalletBalance(balance=float(user.balance), currency=user.currency)


# ── POST /auth/telegram/link-code ──────────────────────────────────


class TelegramLinkCodeResponse(BaseModel):
    code: str
    expires_at: datetime
    bot_link: str | None = None  # t.me/YourBot?start=<code>

    model_config = {"from_attributes": True}


@router.post(
    "/telegram/link-code",
    response_model=TelegramLinkCodeResponse,
    summary="Generate a 6-char code to link your Telegram account",
)
def create_telegram_link_code(
    user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> TelegramLinkCodeResponse:
    """Generate a short-lived (15 min) code the user pastes into the
    Telegram bot with `/link CODE`. Idempotent: subsequent calls
    return a fresh code; old unused codes are not invalidated (the
    bot checks the most-recent on link).

    If the user is already linked, the response still contains a
    fresh code so the dashboard can display it for re-link flows
    after a Telegram unlink.
    """
    code = TelegramLinkCode.generate_code()
    expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=15)
    row = TelegramLinkCode(
        user_id=user.id,
        code=code,
        expires_at=expires_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    bot_link = None
    bot_username = settings.telegram_bot_username
    if bot_username:
        bot_link = f"https://t.me/{bot_username}?start={code}"

    return TelegramLinkCodeResponse(
        code=row.code,
        expires_at=row.expires_at,
        bot_link=bot_link,
    )


@router.delete(
    "/telegram/link-code",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate all outstanding Telegram link codes for this user",
)
def invalidate_telegram_link_codes(
    user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> Response:
    rows = session.exec(
        select(TelegramLinkCode).where(
            TelegramLinkCode.user_id == user.id,
            TelegramLinkCode.is_used == False,  # noqa: E712
        )
    ).all()
    for r in rows:
        r.is_used = True
        session.add(r)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/telegram/link",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink Telegram from this account",
)
def unlink_telegram(
    user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> Response:
    user.telegram_chat_id = None
    user.is_telegram_linked = False
    session.add(user)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
