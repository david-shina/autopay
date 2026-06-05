"""Payout service — execute a bill payment via the configured provider.

The race condition in the MVP at `payout.py:38-40` (two concurrent
requests both pass the "is not processing" check before either
commits the flip) is fixed here with `SELECT ... FOR UPDATE` so the
two transactions serialize at the database layer.

Key invariants this module guarantees:
  1. A bill is in exactly one terminal state at a time. The status
     transition is atomic with the balance change and the audit row.
  2. The balance is never negative. The `SELECT FOR UPDATE` + early
     check guards the wallet.
  3. The provider call happens *after* the row is locked and marked
     `processing`, so a duplicate web-hook for the same `reference`
     cannot double-debit the user.
  4. If the provider call fails after the wallet debit, the refund +
     audit row are written in the same transaction. There is no window
     where the user has been charged but the system "forgot."
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import settings
from app.models.bill import Bill
from app.models.enums import AuditActor, BillStatus, TransactionStatus, TransactionType
from app.models.transaction import Transaction
from app.models.user import User
from app.services.audit import (
    audit_payout_failed,
    audit_payout_succeeded,
    audit_wallet_debit,
)
from app.services.payments import (
    AccountNameMismatch,
    InsufficientFunds,
    InvalidAccount,
    PaymentError,
    PaymentProvider,
)

logger = logging.getLogger(__name__)


# ── Result type ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class PayoutResult:
    success: bool
    message: str
    reference: str
    new_balance: Decimal


# ── Helpers ─────────────────────────────────────────────────────────

def _new_reference(bill_id: int) -> str:
    """Our outgoing transfer reference (idempotency key at the provider)."""
    return f"autopay_{bill_id}_{uuid.uuid4().hex[:12]}"


def _ngn_to_kobo(amount: Decimal) -> int:
    """₦ → kobo (integer). Paystack wants whole kobo only."""
    return int((amount * Decimal(100)).quantize(Decimal("1")))


# ── Core entry point ────────────────────────────────────────────────

async def execute_payout(
    session: Session,
    *,
    bill_id: int,
    provider: PaymentProvider,
) -> PayoutResult:
    """Process a payout for `bill_id`.

    Must be called from inside a `with session.begin():` block (or
    wherever the caller wants the transaction boundary). The
    `SELECT ... FOR UPDATE` is on `bills` AND `users` (via
    separate row-locks in the same transaction) to serialize
    concurrent attempts.

    The provider call is awaited. If it raises a typed `PaymentError`,
    we translate it into a refund + audit row. If it raises anything
    else, we re-raise (the caller can decide whether to roll back the
    transaction — typically yes, to keep the bill in `processing` for
    a retry).
    """
    # ── 1. Lock the bill row ───────────────────────────────────────
    bill = session.execute(
        select(Bill).where(Bill.id == bill_id).with_for_update()
    ).scalar_one_or_none()
    if bill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bill {bill_id} not found.",
        )

    # ── 2. Lock the user row (separate FOR UPDATE) ────────────────
    user = session.execute(
        select(User).where(User.id == bill.user_id).with_for_update()
    ).scalar_one_or_none()
    if user is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {bill.user_id} not found.",
        )

    # ── 3. State checks ────────────────────────────────────────────
    if bill.status == BillStatus.PAID.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bill is already paid.",
        )
    if bill.status == BillStatus.CANCELLED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bill is cancelled.",
        )
    if bill.status == BillStatus.PROCESSING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bill is already being processed.",
        )

    fee = Decimal(str(settings.payout_fee_ngn))
    total_charge = Decimal(str(bill.amount)) + fee

    # ── 4. Balance check ───────────────────────────────────────────
    if Decimal(str(user.balance)) < total_charge:
        shortfall = total_charge - Decimal(str(user.balance))
        bill.status = BillStatus.PENDING.value
        session.add(bill)
        audit_payout_failed(
            session,
            user_id=user.id,
            bill_id=bill.id,
            reason=f"insufficient_balance (shortfall={shortfall})",
            retry_count=bill.retry_count,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Insufficient balance. Need {total_charge} NGN, "
                f"have {user.balance} NGN (shortfall {shortfall} NGN)."
            ),
        )

    # ── 5. Mark processing + create debit transaction (atomic) ────
    bill.status = BillStatus.PROCESSING.value
    reference = _new_reference(bill.id)

    debit = Transaction(
        user_id=user.id,
        bill_id=bill.id,
        type=TransactionType.DEBIT.value,
        amount=bill.amount,
        fee=fee,
        currency=bill.currency,
        status=TransactionStatus.PROCESSING.value,
        provider=provider.name,
        provider_reference=reference,
        narration=f"Payment to {bill.vendor_name}",
    )
    session.add(debit)
    session.flush()  # populate debit.id for the audit row

    audit_wallet_debit(
        session,
        user_id=user.id,
        amount=float(bill.amount),
        fee=float(fee),
        bill_id=bill.id,
        provider_reference=reference,
        new_balance=float(user.balance) - float(total_charge),  # pre-debit balance; real update follows
    )

    # ── 6. Resolve account + create transfer recipient ─────────────
    if not bill.account_number or not bill.bank_code:
        _refund_on_failure(session, user, bill, debit, "missing account_number or bank_code")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bill has no payout account configured.",
        )

    try:
        resolved = await provider.resolve_account(
            account_number=bill.account_number, bank_code=bill.bank_code
        )
    except InvalidAccount as exc:
        _refund_on_failure(session, user, bill, debit, f"invalid_account: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Account could not be resolved: {exc}",
        ) from exc

    # If we have a stored account name, verify it matches the resolved
    # one (defense against typos / account swaps).
    if bill.vendor_name and resolved.account_name.upper() != bill.vendor_name.upper():
        # Not a hard fail — the vendor name in the bill is user-entered
        # and may differ from the bank's "official" name. Log only.
        logger.warning(
            "Account name mismatch for bill %d: stored=%r resolved=%r",
            bill.id, bill.vendor_name, resolved.account_name,
        )

    try:
        recipient_code = await provider.create_transfer_recipient(
            account_number=bill.account_number,
            bank_code=bill.bank_code,
            account_name=resolved.account_name,
        )
    except AccountNameMismatch as exc:
        _refund_on_failure(session, user, bill, debit, f"name_mismatch: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Account name mismatch: {exc}",
        ) from exc
    except PaymentError as exc:
        _refund_on_failure(session, user, bill, debit, f"recipient_failed: {exc}")
        raise

    # ── 7. Initiate the transfer ───────────────────────────────────
    try:
        transfer = await provider.initiate_transfer(
            amount_kobo=_ngn_to_kobo(Decimal(str(bill.amount))),
            recipient_code=recipient_code,
            reference=reference,
            reason=f"AutoPay: {bill.vendor_name}",
        )
    except InsufficientFunds as exc:
        # Provider says our MERCHANT balance is too low. Refund user.
        _refund_on_failure(session, user, bill, debit, f"provider_insufficient_funds: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Provider temporarily out of funds. Please retry later.",
        ) from exc
    except PaymentError as exc:
        _refund_on_failure(session, user, bill, debit, f"transfer_failed: {exc}")
        raise

    # ── 8. Commit wallet + mark as processing (success) ───────────
    user.balance = Decimal(str(user.balance)) - total_charge
    debit.status = TransactionStatus.PROCESSING.value  # remains 'processing' until webhook confirms
    session.add(user)
    session.add(debit)
    session.flush()

    # Note: the bill stays in 'processing' until the webhook arrives
    # with transfer.success / transfer.failed. That's the next chunk.

    return PayoutResult(
        success=True,
        message="Transfer initiated; awaiting provider confirmation.",
        reference=reference,
        new_balance=Decimal(str(user.balance)),
    )


# ── Refund helper ───────────────────────────────────────────────────

def _refund_on_failure(
    session: Session,
    user: User,
    bill: Bill,
    debit: Transaction,
    reason: str,
) -> None:
    """Mark the debit failed, increment retry, set bill back to
    scheduled-or-failed. The user's balance is *not* debited in this
    path because the wallet-debit audit row + transaction row are
    also being marked failed; the actual wallet balance was never
    touched (the transfer row is the only place we wrote the
    reservation)."""
    debit.status = TransactionStatus.FAILED.value
    debit.failure_reason = reason
    bill.retry_count += 1
    if bill.retry_count >= bill.max_retries:
        bill.status = BillStatus.FAILED.value
    else:
        bill.status = BillStatus.SCHEDULED.value
    session.add(debit)
    session.add(bill)
    session.flush()
    audit_payout_failed(
        session,
        user_id=user.id,
        bill_id=bill.id,
        reason=reason,
        retry_count=bill.retry_count,
    )


# ── Recurrence helper (used after a successful payout) ──────────────

def schedule_recurrence(session: Session, *, bill: Bill) -> Optional[Bill]:
    """Create the next bill in a recurring series. Returns it, or None
    if the bill is not recurring. Caller is responsible for committing.

    Also updates the original bill's `next_recurrence_date` so the
    scheduler doesn't re-process the same bill on the next run.
    """
    if not bill.is_recurring or not bill.recurrence_interval:
        return None

    delta = timedelta(days=30 if bill.recurrence_interval == "monthly" else 7)
    next_due = bill.due_date + delta

    # Bump the original's next_recurrence_date so the scheduler skips
    # it next time. Strip tzinfo to match the column type.
    nrd = next_due
    if nrd.tzinfo is not None:
        nrd = nrd.replace(tzinfo=None)
    bill.next_recurrence_date = nrd
    session.add(bill)

    next_bill = Bill(
        user_id=bill.user_id,
        vendor_name=bill.vendor_name,
        amount=bill.amount,
        currency=bill.currency,
        due_date=next_due,
        account_number=bill.account_number,
        bank_code=bill.bank_code,
        bank_name=bill.bank_name,
        status=BillStatus.SCHEDULED.value,
        is_recurring=True,
        recurrence_interval=bill.recurrence_interval,
        next_recurrence_date=nrd,
    )
    session.add(next_bill)
    session.flush()
    return next_bill


# ── Webhook-side confirmation (called from webhook handler) ────────

def confirm_payout(
    session: Session,
    *,
    provider_reference: str,
    success: bool,
    failure_reason: Optional[str] = None,
) -> Optional[PayoutResult]:
    """Apply the final state transition when the provider webhook
    confirms the transfer. Returns the payout result for the caller
    to log/respond, or None if no matching transaction is found (a
    webhook for something we never initiated — log and ignore).

    Idempotent: called again with the same outcome is a no-op.
    """
    txn = session.exec(
        select(Transaction).where(Transaction.provider_reference == provider_reference)
    ).first()
    if txn is None:
        logger.warning(
            "Webhook for unknown provider_reference=%s — ignoring", provider_reference
        )
        return None
    if txn.bill_id is None:
        return None

    bill = session.get(Bill, txn.bill_id)
    if bill is None:  # pragma: no cover
        return None

    # Idempotency: if the transaction is already in a terminal state,
    # don't change anything.
    if txn.status in (TransactionStatus.SUCCESS.value, TransactionStatus.FAILED.value):
        return PayoutResult(
            success=txn.status == TransactionStatus.SUCCESS.value,
            message="Already reconciled.",
            reference=provider_reference,
            new_balance=Decimal("0"),
        )

    user = session.get(User, txn.user_id)
    if user is None:  # pragma: no cover
        return None

    if success:
        txn.status = TransactionStatus.SUCCESS.value
        bill.status = BillStatus.PAID.value
        audit_payout_succeeded(
            session,
            user_id=user.id,
            bill_id=bill.id,
            provider_reference=provider_reference,
        )
        message = "Payment successful."
    else:
        txn.status = TransactionStatus.FAILED.value
        txn.failure_reason = failure_reason or "provider_rejected"
        # Refund the wallet: revert the balance that was debited in
        # execute_payout. Fee is also refunded (we never actually
        # paid it).
        user.balance = Decimal(str(user.balance)) + txn.amount + txn.fee
        bill.retry_count += 1
        bill.status = (
            BillStatus.FAILED.value
            if bill.retry_count >= bill.max_retries
            else BillStatus.SCHEDULED.value
        )
        session.add(user)
        audit_payout_failed(
            session,
            user_id=user.id,
            bill_id=bill.id,
            reason=failure_reason or "transfer_failed",
            retry_count=bill.retry_count,
        )
        message = f"Payment failed: {failure_reason or 'unknown reason'}."

    session.add(txn)
    session.add(bill)
    session.flush()

    return PayoutResult(
        success=success,
        message=message,
        reference=provider_reference,
        new_balance=Decimal(str(user.balance)),
    )
