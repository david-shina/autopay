"""Audit log writer.

Every state-changing business operation calls a function here *within the
same SQLAlchemy session* as the business write, so the audit row and the
business row commit (or rollback) atomically. There is no separate
"async audit worker" — the audit table is the same database.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session

from app.models.audit_log import AuditLog
from app.models.enums import AuditActor, AuditEntityType, AuditEventType


def write_audit(
    session: Session,
    *,
    actor: AuditActor | str,
    event_type: AuditEventType | str,
    user_id: Optional[int] = None,
    entity_type: Optional[AuditEntityType | str] = None,
    entity_id: Optional[int] = None,
    before_state: Optional[dict[str, Any]] = None,
    after_state: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    """Append an audit row to `session` (does NOT commit).

    The caller is responsible for committing. This lets multiple audit
    rows and business writes share a single transaction.
    """
    row = AuditLog(
        user_id=user_id,
        actor=actor.value if isinstance(actor, AuditActor) else actor,
        event_type=event_type.value if isinstance(event_type, AuditEventType) else event_type,
        entity_type=entity_type.value if isinstance(entity_type, AuditEntityType) else entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        event_metadata=metadata,
        ip_address=ip_address,
    )
    session.add(row)
    session.flush()  # populate row.id without committing
    return row


# ── Convenience helpers for common events ───────────────────────────

def audit_user_signup(session: Session, user_id: int, *, ip: str | None = None) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.USER,
        event_type=AuditEventType.USER_SIGNUP,
        user_id=user_id,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        ip_address=ip,
    )


def audit_login(session: Session, user_id: int, *, ip: str | None = None) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.USER,
        event_type=AuditEventType.USER_LOGIN,
        user_id=user_id,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        ip_address=ip,
    )


def audit_logout(session: Session, user_id: int, *, ip: str | None = None) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.USER,
        event_type=AuditEventType.USER_LOGOUT,
        user_id=user_id,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        ip_address=ip,
    )


def audit_wallet_credit(
    session: Session,
    *,
    user_id: int,
    amount: float,
    provider_reference: str,
    new_balance: float,
    actor: str = AuditActor.WEBHOOK,
) -> AuditLog:
    return write_audit(
        session,
        actor=actor,
        event_type=AuditEventType.WALLET_CREDITED,
        user_id=user_id,
        entity_type=AuditEntityType.TRANSACTION,
        after_state={"amount": amount, "balance": new_balance},
        metadata={"provider_reference": provider_reference},
    )


def audit_wallet_debit(
    session: Session,
    *,
    user_id: int,
    amount: float,
    fee: float,
    bill_id: int | None,
    provider_reference: str,
    new_balance: float,
) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.SYSTEM,
        event_type=AuditEventType.PAYOUT_ATTEMPTED,
        user_id=user_id,
        entity_type=AuditEntityType.TRANSACTION,
        entity_id=bill_id,
        after_state={
            "amount": amount,
            "fee": fee,
            "balance": new_balance,
            "provider_reference": provider_reference,
        },
    )


def audit_payout_succeeded(
    session: Session,
    *,
    user_id: int,
    bill_id: int,
    provider_reference: str,
) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.WEBHOOK,
        event_type=AuditEventType.PAYOUT_SUCCEEDED,
        user_id=user_id,
        entity_type=AuditEntityType.BILL,
        entity_id=bill_id,
        metadata={"provider_reference": provider_reference},
    )


def audit_payout_failed(
    session: Session,
    *,
    user_id: int,
    bill_id: int,
    reason: str,
    retry_count: int,
) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.SYSTEM,
        event_type=AuditEventType.PAYOUT_FAILED,
        user_id=user_id,
        entity_type=AuditEntityType.BILL,
        entity_id=bill_id,
        metadata={"reason": reason, "retry_count": retry_count},
    )


def audit_bill_created(
    session: Session,
    *,
    user_id: int,
    bill_id: int,
    amount: float,
    provider: str,
) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.USER,
        event_type=AuditEventType.BILL_CREATED,
        user_id=user_id,
        entity_type=AuditEntityType.BILL,
        entity_id=bill_id,
        after_state={"amount": amount, "provider": provider},
    )


def audit_va_created(
    session: Session,
    *,
    user_id: int,
    va_id: int,
    provider: str,
    account_number: str,
) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.SYSTEM,
        event_type=AuditEventType.VA_CREATED,
        user_id=user_id,
        entity_type=AuditEntityType.VIRTUAL_ACCOUNT,
        entity_id=va_id,
        after_state={"provider": provider, "account_number_last4": account_number[-4:]},
    )


def audit_kyc_bvn_submitted(
    session: Session,
    *,
    user_id: int,
    kyc_id: int,
    bvn_last4: str,
) -> AuditLog:
    return write_audit(
        session,
        actor=AuditActor.USER,
        event_type=AuditEventType.KYC_BVN_SUBMITTED,
        user_id=user_id,
        entity_type=AuditEntityType.KYC_RECORD,
        entity_id=kyc_id,
        after_state={"bvn_last4": bvn_last4},
    )
