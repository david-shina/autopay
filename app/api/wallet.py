"""Wallet API — balance, virtual account provisioning.

Mounted at /api/v1/wallet in `app.main`.

`POST /wallet/provision` is the user-facing escape hatch when signup
could not auto-provision a DVA (Paystack business not approved for
Dedicated NUBANs, transient provider error, etc.). It is idempotent:
a second call after success returns the existing account.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.services.audit import (
    audit_va_created,
    write_audit,
)
from app.services.auth import get_current_active_user
from app.services.payments import (
    PaymentError,
    PaymentProvider,
    get_payment_provider,
)
from app.models.enums import AuditActor, AuditEventType, AuditEntityType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["wallet"])


class VirtualAccountPublic(BaseModel):
    """Wire format for the user's virtual account."""

    account_number: Optional[str] = None
    account_name: Optional[str] = None
    bank_name: Optional[str] = None
    bank_code: Optional[str] = None
    provider: str
    provider_reference: str


class ProvisionResponse(BaseModel):
    virtual_account: VirtualAccountPublic
    already_existed: bool
    message: str


@router.post(
    "/provision",
    response_model=ProvisionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a virtual account for the logged-in user",
)
async def provision_virtual_account(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> ProvisionResponse:
    """Idempotent. If the user already has a VA, returns it (200 OK
    semantically, but 201 status code for both cases — the field
    `already_existed` distinguishes). On a real provider error, returns
    502 with the error in the audit log; the user can retry.
    """
    existing = session.exec(
        select(VirtualAccount).where(VirtualAccount.user_id == user.id)
    ).first()
    if existing is not None:
        return ProvisionResponse(
            virtual_account=VirtualAccountPublic(
                account_number=existing.account_number,
                account_name=existing.account_name,
                bank_name=existing.bank_name,
                bank_code=None,  # not stored on the model today
                provider=existing.provider,
                provider_reference=existing.provider_account_reference,
            ),
            already_existed=True,
            message="Virtual account already provisioned.",
        )

    try:
        customer_code = await provider.create_customer(
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            phone=user.phone_number,
        )
        va_data = await provider.create_virtual_account(
            customer_code=customer_code,
            account_name=f"{user.first_name} {user.last_name}".strip(),
        )
    except PaymentError as exc:
        # Log the failure but expose a clean 502 to the user.
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
                "trigger": "explicit_provision",
            },
        )
        session.commit()
        logger.warning("DVA provision failed for user %d: %s", user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not provision virtual account: {exc}",
        ) from exc

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
    session.commit()
    session.refresh(va)

    return ProvisionResponse(
        virtual_account=VirtualAccountPublic(
            account_number=va.account_number,
            account_name=va.account_name,
            bank_name=va.bank_name,
            bank_code=None,
            provider=va.provider,
            provider_reference=va.provider_account_reference,
        ),
        already_existed=False,
        message="Virtual account provisioned.",
    )
