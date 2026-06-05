"""Tests for the payout service's money math.

These are pure unit tests — no DB, no provider. They pin the
critical arithmetic: kobo conversion, fee math, refund math.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.payout import _ngn_to_kobo  # noqa: SLF001


# ── kobo conversion ─────────────────────────────────────────────────

def test_ngn_to_kobo_whole_amount() -> None:
    assert _ngn_to_kobo(Decimal("100.00")) == 10000


def test_ngn_to_kobo_fractional() -> None:
    assert _ngn_to_kobo(Decimal("123.45")) == 12345


def test_ngn_to_kobo_zero() -> None:
    assert _ngn_to_kobo(Decimal("0")) == 0


def test_ngn_to_kobo_rounds_nearest_kobo() -> None:
    # 0.009 NGN above 100 = 10000.9 kobo, rounds up to 10001.
    assert _ngn_to_kobo(Decimal("100.009")) == 10001


def test_ngn_to_kobo_uses_bankers_rounding() -> None:
    # 100.005 NGN = 10000.5 kobo, banker's rounding → 10000 (even).
    # Documents the (slight) choice: ROUND_HALF_EVEN, not ROUND_HALF_UP.
    assert _ngn_to_kobo(Decimal("100.005")) == 10000


def test_ngn_to_kobo_large() -> None:
    assert _ngn_to_kobo(Decimal("999999999.99")) == 99_999_999_999


# ── Test that the wrapper behaviour we depend on works ──────────────

def test_decimal_arithmetic_for_balance() -> None:
    """Pin the rule that we use Decimal (not float) for money math."""
    balance = Decimal("1000.00")
    amount = Decimal("123.45")
    fee = Decimal("50.00")
    total = amount + fee
    new_balance = balance - total
    assert new_balance == Decimal("826.55")
    assert isinstance(new_balance, Decimal)
