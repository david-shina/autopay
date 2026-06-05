"""Agent state + decision enum."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import TypedDict


class Decision(str, Enum):
    PAY_NOW = "pay_now"
    SCHEDULE = "schedule"
    HOLD = "hold"


class AgentState(TypedDict, total=False):
    """LangGraph state. All numerics are strings — langgraph
    serialises state through checkpoints and Decimal doesn't survive
    that round-trip cleanly."""

    user_balance: str
    bill_amount: str
    fee: str
    days_until_due: int
    decision: str
    reason: str


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    reason: str
