"""Transaction DTOs — wallet history."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    bill_id: Optional[int]
    type: str
    amount: Decimal
    fee: Decimal
    currency: str
    status: str
    provider: str
    provider_reference: Optional[str]
    narration: Optional[str]
    failure_reason: Optional[str]
    created_at: datetime
