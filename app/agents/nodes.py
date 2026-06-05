"""Agent nodes — pure decision function + LangGraph node wrapper.

The decision rule (MVP `app/agents/nodes.py:32-35`):

  if balance < (amount + fee)            → hold
  elif days_until_due <= 3               → pay_now
  else                                    → schedule

`decide()` is a pure function so the rule can be unit-tested without
spinning up LangGraph. The LangGraph node `_make_decision_node` is a
thin wrapper that knows how to read/write `AgentState`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from app.agents.state import AgentState, Decision, DecisionResult
from app.models.bill import Bill
from app.models.user import User


def decide(
    *,
    user_balance: Decimal,
    bill_amount: Decimal,
    fee: Decimal,
    days_until_due: int,
) -> DecisionResult:
    """Pure function. Returns the decision + human-readable reason.

    `days_until_due` can be negative (overdue) — we treat that as
    "pay now" because the bill is already past due.
    """
    total = bill_amount + fee

    if user_balance < total:
        shortfall = total - user_balance
        return DecisionResult(
            decision=Decision.HOLD,
            reason=(
                f"Insufficient balance: need ₦{total}, have ₦{user_balance} "
                f"(shortfall ₦{shortfall}). Hold until top-up."
            ),
        )

    if days_until_due <= 3:
        return DecisionResult(
            decision=Decision.PAY_NOW,
            reason=(
                f"Due in {days_until_due} day(s) (≤3d cutoff). "
                f"Balance is sufficient (₦{user_balance})."
            ),
        )

    return DecisionResult(
        decision=Decision.SCHEDULE,
        reason=(
            f"Due in {days_until_due} day(s) (>3d cutoff). "
            f"Schedule and re-evaluate closer to the due date."
        ),
    )


def decide_for_bill(
    bill: Bill,
    *,
    user: User,
    fee: Decimal,
    now: Optional[datetime] = None,
) -> DecisionResult:
    """Convenience wrapper for the (Bill, User) pair."""
    now = now or datetime.now(tz=timezone.utc)
    days_until_due = (bill.due_date - now).days
    return decide(
        user_balance=Decimal(str(user.balance)),
        bill_amount=Decimal(str(bill.amount)),
        fee=fee,
        days_until_due=days_until_due,
    )


def make_decision_node(state: AgentState) -> AgentState:
    """LangGraph node — applies the rule, writes to `state`."""
    result = decide(
        user_balance=Decimal(state["user_balance"]),
        bill_amount=Decimal(state["bill_amount"]),
        fee=Decimal(state["fee"]),
        days_until_due=int(state["days_until_due"]),
    )
    return {"decision": result.decision.value, "reason": result.reason}
