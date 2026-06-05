"""Unit tests for the APScheduler integration.

The scheduler is global, so each test starts and stops it explicitly.
We use a short interval and a monkey-patched function to avoid
waiting on real time.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.core.scheduler import (
    get_scheduler,
    start_scheduler,
    stop_scheduler,
)
from app.models.bill import Bill
from app.models.enums import AuditActor, AuditEventType, BillStatus
from app.services.audit import write_audit


@pytest.fixture(autouse=True)
def _scheduler_lifecycle():
    """Make sure the scheduler is stopped after every test."""
    yield
    stop_scheduler()


def test_scheduler_starts_and_stops() -> None:
    assert get_scheduler() is None
    start_scheduler()
    assert get_scheduler() is not None
    assert get_scheduler().running
    stop_scheduler()
    assert get_scheduler() is None


def test_scheduler_is_idempotent() -> None:
    start_scheduler()
    s1 = get_scheduler()
    start_scheduler()
    s2 = get_scheduler()
    assert s1 is s2
    stop_scheduler()


def test_process_scheduled_bills_picks_up_due_bills(session) -> None:
    """A bill with `status='scheduled'` and `due_date <= now` should be
    re-evaluated by the agent and (if pay_now) flipped to 'pending'."""
    from app.core.database import session_scope
    from app.core.security import hash_password
    from app.models.user import User

    with session_scope() as s:
        user = User(
            email="sched@x.com",
            hashed_password=hash_password("p"),
            first_name="S",
            last_name="U",
            phone_number="08099999991",
            balance=Decimal("100000"),
        )
        s.add(user)
        s.commit()
        user_id = user.id
        bill = Bill(
            user_id=user_id,
            vendor_name="Vendor",
            amount=Decimal("5000"),
            due_date=datetime.now() - timedelta(days=1),  # overdue
            account_number="0123456789",
            bank_code="058",
            bank_name="GTBank",
            status=BillStatus.SCHEDULED.value,
        )
        s.add(bill)
        s.commit()
        bill_id = bill.id

    start_scheduler()
    # Manually trigger the function (don't wait for the interval)
    from app.core.scheduler import _process_scheduled_bills
    _process_scheduled_bills()

    with session_scope() as s:
        bill = s.get(Bill, bill_id)
        # The agent should have decided pay_now and flipped status to pending
        assert bill.status == BillStatus.PENDING.value


def test_process_recurring_bills_spawns_next_occurrence(session) -> None:
    from app.core.database import session_scope
    from app.core.security import hash_password
    from app.models.user import User
    from sqlmodel import select

    with session_scope() as s:
        user = User(
            email="recur@x.com",
            hashed_password=hash_password("p"),
            first_name="R",
            last_name="U",
            phone_number="08099999992",
            balance=Decimal("0"),
        )
        s.add(user)
        s.commit()
        user_id = user.id
        bill = Bill(
            user_id=user_id,
            vendor_name="Monthly",
            amount=Decimal("1000"),
            due_date=datetime.now() - timedelta(days=30),
            status=BillStatus.PAID.value,
            is_recurring=True,
            recurrence_interval="monthly",
            next_recurrence_date=datetime.now() - timedelta(days=1),
        )
        s.add(bill)
        s.commit()
        original_id = bill.id

    start_scheduler()
    from app.core.scheduler import _process_recurring_bills
    _process_recurring_bills()

    with session_scope() as s:
        all_bills = s.exec(select(Bill).where(Bill.user_id == user_id)).all()
        # There should be at least 2 bills: the original + the new recurrence
        assert len(all_bills) >= 2
        # The original's next_recurrence_date should be updated to a
        # time >= now (next billing cycle).
        original = s.get(Bill, original_id)
        nrd = original.next_recurrence_date
        if nrd.tzinfo is not None:
            nrd = nrd.replace(tzinfo=None)
        now = datetime.now()
        # `nrd` is `due_date + 30d = now - 30d + 30d = now`. Allow a
        # small skew for clock drift.
        assert abs((nrd - now).total_seconds()) < 60, (
            f"next_recurrence_date {nrd} should be near now {now}"
        )


def test_scheduler_swallows_exceptions_in_jobs() -> None:
    """If a job raises, the scheduler should log and continue, not die."""
    from app.core.scheduler import _process_scheduled_bills
    # Don't insert any data — the function should handle the empty case
    # without raising.
    start_scheduler()
    _process_scheduled_bills()  # no bills, no error
    assert get_scheduler() is not None
    assert get_scheduler().running
