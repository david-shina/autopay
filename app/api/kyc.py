"""KYC API — BVN submission.

Mounted at /api/v1/kyc in `app.main`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.kyc import KycRecord
from app.models.user import User
from app.schemas.kyc import KycStatusResponse, KycSubmitRequest
from app.services.audit import audit_kyc_bvn_submitted
from app.services.auth import get_current_active_user
from app.services.crypto import (
    BVNKeyError,
    encrypt_bvn,
    hash_bvn,
    last4,
)

router = APIRouter(tags=["kyc"])


@router.post(
    "/bvn",
    response_model=KycStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_bvn(
    payload: KycSubmitRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> KycStatusResponse:
    """Encrypt the BVN with Fernet, store its hash + last4, write audit."""
    existing = session.exec(
        select(KycRecord).where(KycRecord.user_id == user.id)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="KYC already submitted for this user.",
        )

    try:
        ciphertext = encrypt_bvn(payload.bvn)
        bvn_hash = hash_bvn(payload.bvn)
    except BVNKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"KYC encryption not configured: {exc}",
        ) from exc

    record = KycRecord(
        user_id=user.id,
        bvn_ciphertext=ciphertext,
        bvn_last4=last4(payload.bvn),
        bvn_hash=bvn_hash,
        bvn_validated=False,
    )
    session.add(record)
    session.flush()
    audit_kyc_bvn_submitted(
        session,
        user_id=user.id,
        kyc_id=record.id,
        bvn_last4=record.bvn_last4,
    )
    session.commit()
    session.refresh(record)
    return KycStatusResponse.model_validate(record)


@router.get("/bvn", response_model=KycStatusResponse)
def get_kyc(
    session: Session = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> KycStatusResponse:
    record = session.exec(
        select(KycRecord).where(KycRecord.user_id == user.id)
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="No KYC on file.")
    return KycStatusResponse.model_validate(record)
