"""Bills API — upload, list, get, pay, cancel.

Mounted at /api/v1/bills in `app.main`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlmodel import Session, select

from app.agents.graphs import run_agent
from app.agents.state import Decision
from app.core.config import settings
from app.core.database import get_session
from app.models.bill import Bill
from app.models.enums import BillStatus
from app.models.user import User
from app.schemas.bill import (
    BillActionResponse,
    BillCreateRequest,
    BillResponse,
)
from app.services.audit import audit_bill_created
from app.services.auth import get_current_active_user
from app.services.date_parser import parse_bill_due_date
from app.services.loaders import loader_from_upload
from app.services.payments import PaymentProvider, get_payment_provider
from app.services.payout import execute_payout

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bills"])


# ── POST /bills/upload ──────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=BillActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_bill(
    request_bill: Optional[str] = Form(
        default=None,
        description="Raw bill text (alternative to uploading a file)",
    ),
    file: Optional[UploadFile] = File(default=None),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> BillActionResponse:
    """Upload a bill (PDF / image) OR paste text, get back an
    extracted + agent-decided bill in the database."""
    if not file and not request_bill:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either a file upload or a 'request_bill' text field.",
        )

    # 1. Extract bill info
    if file is not None:
        data = await file.read()
        loader = loader_from_upload(
            filename=file.filename or "",
            content_type=file.content_type,
            data=data,
        )
    else:
        loader = loader_from_upload(
            filename="text.txt",
            content_type="text/plain",
            data=(request_bill or "").encode("utf-8"),
        )

    try:
        extracted = await loader.extract()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to extract bill: {exc}",
        ) from exc

    if not extracted.vendor_name or float(extracted.amount) <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Could not determine vendor and amount. "
                "Provide 'request_bill' text or upload a clearer file."
            ),
        )

    # 2. Create the bill
    bill = Bill(
        user_id=user.id,
        vendor_name=extracted.vendor_name,
        amount=Decimal(str(extracted.amount)),
        currency=extracted.currency or "NGN",
        due_date=parse_bill_due_date(extracted.due_date),
        account_number=extracted.account_number,
        bank_code=extracted.bank_code,
        bank_name=extracted.bank_name,
        status=BillStatus.PENDING.value,
    )
    session.add(bill)
    session.flush()
    audit_bill_created(
        session,
        user_id=user.id,
        bill_id=bill.id,
        amount=float(bill.amount),
        provider="paystack",  # we only ship with Paystack for now
    )

    # 3. Run the decision agent
    days_until_due = (bill.due_date - datetime.now()).days
    decision = run_agent(
        user_balance=Decimal(str(user.balance)),
        bill_amount=Decimal(str(bill.amount)),
        fee=Decimal(str(settings.payout_fee_ngn)),
        days_until_due=days_until_due,
    )

    # 4. If agent says pay_now, run the payout
    message = "Bill created."
    if decision.decision == Decision.PAY_NOW:
        try:
            result = await execute_payout(session, bill_id=bill.id, provider=provider)
            session.commit()
            message = result.message
        except HTTPException:
            session.rollback()
            # Payout refused (insufficient funds, etc). Bill is still
            # in the DB; the user can /pay it again later.
            message = "Bill created; payout deferred (see decision reason)."
    elif decision.decision == Decision.SCHEDULE:
        bill.status = BillStatus.SCHEDULED.value
        session.add(bill)
        message = "Bill scheduled for later evaluation."

    session.commit()
    session.refresh(bill)

    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message=message,
        decision=decision.decision.value,
        decision_reason=decision.reason,
    )


# ── POST /bills (no upload) ─────────────────────────────────────────

@router.post(
    "",
    response_model=BillActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_bill(
    payload: BillCreateRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> BillActionResponse:
    bill = Bill(
        user_id=user.id,
        vendor_name=payload.vendor_name,
        amount=Decimal(str(payload.amount)),
        currency="NGN",
        due_date=payload.due_date,
        account_number=payload.account_number,
        bank_code=payload.bank_code,
        bank_name=payload.bank_name,
        status=BillStatus.PENDING.value,
    )
    session.add(bill)
    session.flush()
    audit_bill_created(
        session,
        user_id=user.id,
        bill_id=bill.id,
        amount=float(bill.amount),
        provider="paystack",
    )
    session.commit()
    session.refresh(bill)
    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message="Bill created.",
    )


# ── GET /bills ──────────────────────────────────────────────────────

@router.get("", response_model=list[BillResponse])
def list_bills(
    status_filter: Optional[str] = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> list[BillResponse]:
    q = select(Bill).where(Bill.user_id == user.id)
    if status_filter:
        q = q.where(Bill.status == status_filter)
    q = q.order_by(Bill.due_date.asc())
    rows = session.exec(q).all()
    return [BillResponse.model_validate(r) for r in rows]


# ── GET /bills/{id} ─────────────────────────────────────────────────

@router.get("/{bill_id}", response_model=BillResponse)
def get_bill(
    bill_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> BillResponse:
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")
    return BillResponse.model_validate(bill)


# ── POST /bills/{id}/pay ────────────────────────────────────────────

@router.post("/{bill_id}/pay", response_model=BillActionResponse)
async def pay_bill(
    bill_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> BillActionResponse:
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")

    try:
        result = await execute_payout(session, bill_id=bill.id, provider=provider)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment provider error. Please try again later.",
        )

    bill = session.get(Bill, bill.id)  # refresh
    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message=result.message,
        decision=Decision.PAY_NOW.value,
    )


# ── POST /bills/{id}/cancel ─────────────────────────────────────────

@router.post("/{bill_id}/cancel", response_model=BillActionResponse)
def cancel_bill(
    bill_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> BillActionResponse:
    bill = session.get(Bill, bill_id)
    if bill is None or bill.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status in (BillStatus.PAID.value, BillStatus.PROCESSING.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a bill in status '{bill.status}'.",
        )
    bill.status = BillStatus.CANCELLED.value
    session.add(bill)
    session.commit()
    session.refresh(bill)
    return BillActionResponse(
        bill=BillResponse.model_validate(bill),
        message="Bill cancelled.",
    )
