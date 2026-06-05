"""Tests for the LangGraph decision agent.

The rule (from `app/agents/nodes.py`):
  - balance < (amount + fee)       -> hold
  - days_until_due <= 3            -> pay_now
  - otherwise                      -> schedule
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.graphs import run_agent
from app.agents.nodes import decide, decide_for_bill
from app.agents.state import Decision, DecisionResult
from app.models.bill import Bill
from app.models.user import User


FEE = Decimal("50.00")


# ── decide() pure function ─────────────────────────────────────────

def test_decide_holds_when_balance_below_total() -> None:
    out = decide(
        user_balance=Decimal("100"),
        bill_amount=Decimal("200"),
        fee=FEE,
        days_until_due=10,
    )
    assert out.decision is Decision.HOLD
    assert "Insufficient" in out.reason


def test_decide_pays_now_when_due_in_3_days() -> None:
    out = decide(
        user_balance=Decimal("1000"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=3,
    )
    assert out.decision is Decision.PAY_NOW


def test_decide_pays_now_when_due_today() -> None:
    out = decide(
        user_balance=Decimal("1000"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=0,
    )
    assert out.decision is Decision.PAY_NOW


def test_decide_pays_now_when_overdue() -> None:
    out = decide(
        user_balance=Decimal("1000"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=-5,
    )
    assert out.decision is Decision.PAY_NOW


def test_decide_schedules_when_due_far_out() -> None:
    out = decide(
        user_balance=Decimal("1000"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=14,
    )
    assert out.decision is Decision.SCHEDULE


def test_decide_boundary_4_days_is_schedule() -> None:
    """4 days is strictly greater than the 3-day cutoff → schedule."""
    out = decide(
        user_balance=Decimal("1000"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=4,
    )
    assert out.decision is Decision.SCHEDULE


def test_decide_exact_balance_does_not_hold() -> None:
    """Balance == total → not 'less than', so not HOLD."""
    out = decide(
        user_balance=Decimal("550"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=10,
    )
    assert out.decision is Decision.SCHEDULE


def test_decide_off_by_one_kobo_does_hold() -> None:
    out = decide(
        user_balance=Decimal("549.99"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=10,
    )
    assert out.decision is Decision.HOLD


# ── run_agent (LangGraph wrapper) ──────────────────────────────────

def test_run_agent_matches_pure_decide() -> None:
    """The graph must produce the same answer as `decide()`."""
    direct: DecisionResult = decide(
        user_balance=Decimal("1000"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=2,
    )
    graph_out = run_agent(
        user_balance=Decimal("1000"),
        bill_amount=Decimal("500"),
        fee=FEE,
        days_until_due=2,
    )
    assert graph_out.decision is direct.decision
    assert graph_out.reason == direct.reason


# ── decide_for_bill(Bill, User) wrapper ────────────────────────────

def test_decide_for_bill_uses_bill_due_date() -> None:
    from datetime import datetime, timedelta, timezone

    user = User(id=1, first_name="a", last_name="b", email="a@b.com",
                phone_number="0801", hashed_password="x", balance=Decimal("1000"))
    bill = Bill(
        id=1, user_id=1, vendor_name="PHCN",
        amount=Decimal("100"),
        due_date=datetime.now(tz=timezone.utc) + timedelta(days=2),
    )
    out = decide_for_bill(bill, user=user, fee=FEE)
    assert out.decision is Decision.PAY_NOW
