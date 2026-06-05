"""KYC DTOs — BVN submission."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KycSubmitRequest(BaseModel):
    """BVN is 11 digits in Nigeria. We don't store the plaintext — see
    `KycRecord` model. The response exposes only the last 4 digits."""

    bvn: str = Field(min_length=11, max_length=11, pattern=r"^\d{11}$")


class KycStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    bvn_last4: str
    bvn_validated: bool
    validated_at: Optional[datetime]
    created_at: datetime
