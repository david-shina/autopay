"""Unit tests for the bill-due-date parser.

The LLM hands us dates in every format under the sun. The parser must
return a valid `datetime` for *any* input, never raise, and prefer
`now()` over raising for unparseable input so a bill can always be
created.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.date_parser import parse_bill_due_date


# ── None / empty ─────────────────────────────────────────────────────

def test_none_returns_now() -> None:
    before = datetime.now()
    result = parse_bill_due_date(None)
    after = datetime.now()
    assert before <= result <= after


def test_empty_string_returns_now() -> None:
    before = datetime.now()
    result = parse_bill_due_date("")
    after = datetime.now()
    assert before <= result <= after


def test_whitespace_string_returns_now() -> None:
    result = parse_bill_due_date("   ")
    delta = abs((result - datetime.now()).total_seconds())
    assert delta < 2


# ── ISO 8601 ────────────────────────────────────────────────────────

def test_iso_date_only() -> None:
    result = parse_bill_due_date("2099-12-31")
    assert result == datetime(2099, 12, 31)


def test_iso_datetime() -> None:
    result = parse_bill_due_date("2099-12-31T15:30:00")
    assert result == datetime(2099, 12, 31, 15, 30, 0)


def test_iso_with_microseconds() -> None:
    result = parse_bill_due_date("2099-12-31T15:30:00.123456")
    assert result.year == 2099
    assert result.month == 12
    assert result.day == 31


def test_iso_with_trailing_z() -> None:
    # "Z" is the military/aviation form of "+00:00". We strip it
    # before fromisoformat parses.
    result = parse_bill_due_date("2099-12-31T15:30:00Z")
    assert result.year == 2099
    assert result.day == 31


# ── DD/MM/YYYY (Nigerian format) ────────────────────────────────────

def test_dd_slash_mm_yyyy() -> None:
    result = parse_bill_due_date("15/03/2099")
    assert result == datetime(2099, 3, 15)


def test_dd_dash_mm_yyyy() -> None:
    result = parse_bill_due_date("15-03-2099")
    assert result == datetime(2099, 3, 15)


def test_dd_slash_mm_yy_two_digit_year() -> None:
    # Python's %y maps 00-68 -> 2000-2068, 69-99 -> 1969-1999.
    # We use 30 (-> 2030) so the result is unambiguously in the future.
    result = parse_bill_due_date("15/03/30")
    assert result == datetime(2030, 3, 15)


# ── MM/DD/YYYY (American fallback) ──────────────────────────────────

def test_mm_slash_dd_yyyy() -> None:
    # 03/15 in DD/MM format is invalid (no month 15), so parser falls
    # through to MM/DD and gets March 15.
    result = parse_bill_due_date("03/15/2099")
    assert result == datetime(2099, 3, 15)


# ── Month name formats ──────────────────────────────────────────────

def test_full_month_name() -> None:
    result = parse_bill_due_date("15 March 2099")
    assert result == datetime(2099, 3, 15)


def test_short_month_name() -> None:
    result = parse_bill_due_date("15 Mar 2099")
    assert result == datetime(2099, 3, 15)


def test_month_comma_day_year() -> None:
    result = parse_bill_due_date("March 15, 2099")
    assert result == datetime(2099, 3, 15)


# ── Relative phrases ────────────────────────────────────────────────

def test_today() -> None:
    result = parse_bill_due_date("today")
    assert result.date() == datetime.now().date()


def test_tomorrow() -> None:
    result = parse_bill_due_date("tomorrow")
    assert result.date() == (datetime.now() + timedelta(days=1)).date()


def test_in_2_weeks() -> None:
    result = parse_bill_due_date("in 2 weeks")
    expected = (datetime.now() + timedelta(weeks=2)).date()
    assert result.date() == expected


def test_5_days() -> None:
    result = parse_bill_due_date("5 days")
    expected = (datetime.now() + timedelta(days=5)).date()
    assert result.date() == expected


def test_word_number_ten_days() -> None:
    result = parse_bill_due_date("ten days")
    expected = (datetime.now() + timedelta(days=10)).date()
    assert result.date() == expected


def test_next_week() -> None:
    result = parse_bill_due_date("next week")
    expected = (datetime.now() + timedelta(weeks=1)).date()
    assert result.date() == expected


# ── Past dates clamp to now() ───────────────────────────────────────

def test_past_iso_date_clamps_to_now() -> None:
    result = parse_bill_due_date("2000-01-01")
    delta = abs((result - datetime.now()).total_seconds())
    assert delta < 2


def test_past_yesterday_clamps_to_now() -> None:
    result = parse_bill_due_date("yesterday")
    delta = abs((result - datetime.now()).total_seconds())
    assert delta < 2


# ── Pass-through datetime ───────────────────────────────────────────

def test_datetime_passthrough() -> None:
    dt = datetime(2099, 6, 15, 12, 0, 0)
    assert parse_bill_due_date(dt) == dt


# ── Garbage input ───────────────────────────────────────────────────

@pytest.mark.parametrize("garbage", [
    "not a date",
    "/////",
    "abc123",
    "   -- --   ",
    "##",
])
def test_garbage_falls_back_to_now(garbage: str) -> None:
    result = parse_bill_due_date(garbage)
    delta = abs((result - datetime.now()).total_seconds())
    assert delta < 2, f"expected near-now, got {result} for input {garbage!r}"


# ── Whitespace tolerance ────────────────────────────────────────────

def test_strips_whitespace() -> None:
    result = parse_bill_due_date("   2099-12-31   ")
    assert result == datetime(2099, 12, 31)


def test_trailing_period_ignored_for_relative() -> None:
    result = parse_bill_due_date("in 3 days.")
    expected = (datetime.now() + timedelta(days=3)).date()
    assert result.date() == expected
