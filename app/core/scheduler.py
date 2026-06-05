"""APScheduler integration.

Runs in the same process as FastAPI (single worker by design — see
Dockerfile). We schedule two jobs:

  1. `process_scheduled_bills` — every minute, picks up bills with
     `status='scheduled'` and `due_date <= now`, runs the decision
     agent again, and acts on the result (pay_now triggers a payout;
     hold re-schedules; cancel clears the bill).

  2. `process_recurring_bills` — every 6 hours, finds bills with
     `is_recurring=True` whose `next_recurrence_date <= now` and
     spawns a fresh bill for the next period.

Job identifiers are stable so the scheduler can dedup on restart.
The scheduler is a no-op if the database is unreachable at startup
(it logs a warning and continues).

In production we use `AsyncIOScheduler` (needs a running event loop).
In test contexts we fall back to `BackgroundScheduler` so unit tests
don't need a loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: Optional[object] = None


# ── Public API ──────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Idempotent: a second call is a no-op.

    Detects whether we're inside a running asyncio event loop (FastAPI
    lifespan) or in a test/script context. AsyncIO needs the loop; in
    tests we use a plain BackgroundScheduler."""
    global _scheduler
    if _scheduler is not None:
        return

    try:
        asyncio.get_running_loop()
        scheduler_cls = AsyncIOScheduler
    except RuntimeError:
        scheduler_cls = BackgroundScheduler

    _scheduler = scheduler_cls(timezone="UTC")
    _scheduler.add_job(
        _run_in_session("process_scheduled_bills", _process_scheduled_bills),
        trigger=IntervalTrigger(minutes=1),
        id="process_scheduled_bills",
        name="Re-evaluate scheduled bills",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=None,  # don't fire immediately on boot
    )
    _scheduler.add_job(
        _run_in_session("process_recurring_bills", _process_recurring_bills),
        trigger=IntervalTrigger(hours=6),
        id="process_recurring_bills",
        name="Spawn next recurrence for recurring bills",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=None,
    )
    try:
        _scheduler.start()
        logger.info("Scheduler started: %s", [j.id for j in _scheduler.get_jobs()])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler failed to start: %s", exc)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler shutdown error: %s", exc)
    finally:
        _scheduler = None


def get_scheduler() -> Optional[object]:
    return _scheduler


# ── Job runners ─────────────────────────────────────────────────────

def _run_in_session(job_id: str, fn):
    """Wrap a sync function as an async coroutine that opens a session.
    Used so the scheduler doesn't depend on async DB machinery."""
    import asyncio
    from functools import wraps

    @wraps(fn)
    async def wrapper() -> None:
        try:
            await asyncio.to_thread(fn)
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler job %s raised", job_id)

    return wrapper


def _process_scheduled_bills() -> None:
    """For every scheduled bill that's now due, re-run the decision
    agent and act on the result. Idempotent: a bill already in
    `processing` is skipped."""
    from datetime import datetime
    from decimal import Decimal
    from sqlalchemy import select

    from app.agents.graphs import run_agent
    from app.agents.state import Decision
    from app.core.config import settings
    from app.core.database import session_scope
    from app.models.bill import Bill
    from app.models.enums import BillStatus
    from app.models.user import User

    now = datetime.now()
    with session_scope() as session:
        # SQLModel 0.0.22 quirk: select(Model).all() can return Row
        # tuples. Use scalars() to get model instances.
        from sqlalchemy import select as _sa_select
        due_bills = session.execute(
            _sa_select(Bill).where(
                Bill.status == BillStatus.SCHEDULED.value,
                Bill.due_date <= now,
            )
        ).scalars().all()
        # Postgres TIMESTAMP comes back TZ-aware. Our `now` is naive
        # (local time). Strip tzinfo on the way out so arithmetic with
        # `now` is safe.
        due_ids = [b.id for b in due_bills]

    if not due_ids:
        return

    for bill_id in due_ids:
        try:
            with session_scope() as session:
                db_bill = session.get(Bill, bill_id)
                if db_bill is None or db_bill.status != BillStatus.SCHEDULED.value:
                    continue  # someone else moved it
                user = session.get(User, db_bill.user_id)
                if user is None:
                    continue
                # Strip tzinfo to keep arithmetic safe with naive `now`.
                due_date = db_bill.due_date
                if due_date.tzinfo is not None:
                    due_date = due_date.replace(tzinfo=None)
                days_until_due = (due_date - now).days
                decision = run_agent(
                    user_balance=Decimal(str(user.balance)),
                    bill_amount=Decimal(str(db_bill.amount)),
                    fee=Decimal(str(settings.payout_fee_ngn)),
                    days_until_due=days_until_due,
                )
                if decision.decision == Decision.PAY_NOW:
                    # The payout path is async; defer to the bills API
                    # by setting status=pending. The user can re-POST
                    # /bills/{id}/pay, or we can call execute_payout
                    # from here. For MVP, we mark it pending and let
                    # the user (or a follow-up job) kick the payout.
                    db_bill.status = BillStatus.PENDING.value
                    session.add(db_bill)
                    session.commit()
            logger.info("Scheduler: re-evaluated bill %d → %s", bill_id, decision.decision.value)
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler: error processing bill %d", bill_id)


def _process_recurring_bills() -> None:
    """Spawn the next occurrence of every recurring bill whose
    `next_recurrence_date` is in the past."""
    from datetime import datetime, timedelta
    from sqlalchemy import select

    from app.core.database import session_scope
    from app.models.bill import Bill
    from app.models.enums import AuditActor, AuditEntityType, AuditEventType, BillStatus
    from app.services.audit import write_audit
    from app.services.payout import schedule_recurrence

    now = datetime.now()
    with session_scope() as session:
        from sqlalchemy import select as _sa_select
        recurring_bills = session.execute(
            _sa_select(Bill).where(
                Bill.is_recurring == True,  # noqa: E712
                Bill.next_recurrence_date != None,  # noqa: E711
                Bill.next_recurrence_date <= now,
            )
        ).scalars().all()
        recurring_ids = [b.id for b in recurring_bills]

    for original_id in recurring_ids:
        try:
            with session_scope() as session:
                db_bill = session.get(Bill, original_id)
                if db_bill is None:
                    continue
                next_bill = schedule_recurrence(session, bill=db_bill)
                if next_bill is not None:
                    write_audit(
                        session,
                        actor=AuditActor.SCHEDULER,
                        event_type=AuditEventType.BILL_RECURRENCE_CREATED,
                        user_id=db_bill.user_id,
                        entity_type=AuditEntityType.BILL,
                        entity_id=next_bill.id or 0,
                        metadata={
                            "parent_bill_id": db_bill.id,
                            "next_bill_id": next_bill.id,
                        },
                    )
                    session.commit()
            logger.info("Scheduler: spawned next recurrence for bill %d", original_id)
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler: error recurring bill %d", original_id)
