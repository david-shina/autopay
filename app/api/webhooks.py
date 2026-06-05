"""Paystack webhook handler.

Mounted at /webhooks/paystack in `app.main`.

CRITICAL: Paystack POSTs the *raw* request body. We MUST verify
the HMAC-SHA512 `x-paystack-signature` header BEFORE parsing JSON —
this prevents attackers from forging charge.success events.

REPLAY DEFENSE: We dedup on (provider, event_id) via the
`webhook_events` table. Paystack retries the same event on a network
blip; the second delivery is a 200 no-op with an audit breadcrumb.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.enums import AuditActor, AuditEntityType, AuditEventType, TransactionStatus
from app.models.transaction import Transaction
from app.models.user import User
from app.models.webhook_event import WebhookEvent as WebhookEventRow
from app.services.audit import (
    audit_wallet_credit,
    write_audit,
)
from app.services.payments import (
    PaymentProvider,
    WebhookSignatureError,
    get_payment_provider,
)
from app.services.payout import confirm_payout

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/paystack", status_code=status.HTTP_200_OK)
async def paystack_webhook(
    request: Request,
    session: Session = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> dict:
    """Receive + verify + dispatch a Paystack webhook event."""
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature") or ""

    try:
        event = await provider.parse_webhook(
            raw_body=raw_body, signature_header=signature
        )
    except WebhookSignatureError as exc:
        logger.warning("Paystack webhook with bad signature: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        ) from exc

    # Replay defense: the (provider, event_id) UNIQUE constraint on
    # webhook_events makes a second delivery of the same event a 200
    # no-op. Race-safe via the UNIQUE constraint + IntegrityError.
    try:
        session.add(
            WebhookEventRow(
                provider=provider.name,
                event_id=event.event_id,
                event_type=event.event_type,
            )
        )
        session.flush()
    except IntegrityError:
        session.rollback()
        write_audit(
            session,
            actor=AuditActor.WEBHOOK,
            event_type=AuditEventType.WEBHOOK_REPLAY,
            user_id=None,
            entity_type=AuditEntityType.TRANSACTION,
            entity_id=None,
            metadata={
                "provider": provider.name,
                "event_id": event.event_id,
                "event_type": event.event_type,
            },
        )
        session.commit()
        logger.info("Paystack webhook replay rejected: %s", event.event_id)
        return {"received": True, "replay": True, "event": event.event_type}

    logger.info(
        "Paystack webhook: event=%s reference=%s amount_kobo=%s",
        event.event_type, event.provider_reference, event.amount_kobo,
    )

    if event.event_type == "charge.success":
        _handle_charge_success(session, event)
    elif event.event_type in ("transfer.success", "transfer.failed", "transfer.reversed"):
        _handle_transfer_update(session, event)
    elif event.event_type == "dedicatedaccount.assign.success":
        _handle_dva_assigned(session, event)
    else:
        write_audit(
            session,
            actor=AuditActor.WEBHOOK,
            event_type=AuditEventType.WEBHOOK_UNKNOWN,
            user_id=None,
            entity_type=AuditEntityType.TRANSACTION,
            entity_id=None,
            metadata={
                "provider": provider.name,
                "event_type": event.event_type,
                "reference": event.provider_reference,
                "event_id": event.event_id,
            },
        )
        session.commit()

    return {"received": True, "event": event.event_type}


# ── Handlers ────────────────────────────────────────────────────────

def _handle_charge_success(session: Session, event) -> None:
    """User's VA received money. Credit their wallet and update txn.

    Idempotent: a second charge.success for the same reference is a
    no-op (caught by the dedup at the route layer, plus this status
    check as belt-and-suspenders).
    """
    if not event.provider_reference:
        logger.warning("charge.success with no reference: %s", event.raw)
        return

    txn = session.exec(
        select(Transaction).where(Transaction.provider_reference == event.provider_reference)
    ).first()
    if txn is None:
        # No matching transaction — the top-up arrived before our app
        # created a row. Log and skip.
        write_audit(
            session,
            actor=AuditActor.WEBHOOK,
            event_type=AuditEventType.WALLET_CREDITED,
            user_id=None,
            entity_type=AuditEntityType.TRANSACTION,
            entity_id=None,
            metadata={
                "reference": event.provider_reference,
                "amount_kobo": event.amount_kobo,
                "status": "orphan_credit",
            },
        )
        session.commit()
        return

    if txn.status == TransactionStatus.SUCCESS.value:
        return  # already applied

    user = session.get(User, txn.user_id)
    if user is None:
        return

    amount = Decimal(str(event.amount_kobo or 0)) / Decimal(100)
    user.balance = Decimal(str(user.balance)) + amount
    txn.status = TransactionStatus.SUCCESS.value
    session.add(user)
    session.add(txn)
    audit_wallet_credit(
        session,
        user_id=user.id,
        amount=float(amount),
        provider_reference=event.provider_reference,
        new_balance=float(user.balance),
    )
    session.commit()


def _handle_transfer_update(session: Session, event) -> None:
    """Our outbound transfer completed / failed / was reversed."""
    success = event.event_type == "transfer.success"
    failure_reason: Optional[str] = None
    if not success:
        failure_reason = event.event_type  # "transfer.failed" | "transfer.reversed"

    confirm_payout(
        session,
        provider_reference=event.provider_reference,
        success=success,
        failure_reason=failure_reason,
    )
    session.commit()


def _handle_dva_assigned(session: Session, event) -> None:
    """DVA was successfully assigned. Signup already created the row;
    this is mostly an audit-log breadcrumb."""
    write_audit(
        session,
        actor=AuditActor.WEBHOOK,
        event_type=AuditEventType.VA_CREATED,
        user_id=None,
        entity_type=AuditEntityType.VIRTUAL_ACCOUNT,
        entity_id=None,
        metadata={"event": "dva_assigned", "reference": event.provider_reference, "event_id": event.event_id},
    )
    session.commit()
