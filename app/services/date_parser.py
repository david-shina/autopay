"""Robust date parsing for LLM-extracted bill due dates.

LLMs return dates in every format under the sun. Rather than fight
Pydantic + SQLAlchemy over the type, we accept whatever the loader
hands us (a `str`, a `datetime`, or `None`) and coerce it to a
`datetime` here. Always returns naive `datetime.now()` on failure
so the bill can still be created (with a logged warning).

Accepts:
  * `None` / empty string        -> datetime.now()
  * ISO 8601                     -> "2026-03-15", "2026-03-15T10:30:00"
  * DD/MM/YYYY, DD-MM-YYYY       -> "15/03/2026"  (Nigerian format)
  * MM/DD/YYYY, MM-DD-YYYY       -> "03/15/2026"
  * "15 March 2026" / "Mar 15 2026"
  * Relative phrases             -> "today", "tomorrow", "in 2 weeks"
  * Past-dated input             -> clamped to datetime.now() with a
                                   warning (a bill due yesterday is
                                   almost certainly an OCR mistake;
                                   we'd rather bill-today than error)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Union

logger = logging.getLogger(__name__)


# ── Public API ───────────────────────────────────────────────────────

def parse_bill_due_date(raw: Union[None, str, datetime]) -> datetime:
    """Best-effort parse. Never raises; falls back to `datetime.now()`."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return _now()

    if isinstance(raw, datetime):
        return _clamp_to_present(raw)

    s = raw.strip()
    # Strip trailing time / TZ that confuse some formats
    s = s.replace("Z", "+00:00")

    # 1) ISO 8601 (full datetime, date-only, with/without time, with TZ)
    parsed = _try_iso(s)
    if parsed is not None:
        return _clamp_to_present(parsed)

    # 2) Common slash- and dash-separated formats. Try DD/MM first
    #    (Nigerian default) before MM/DD — the LLM sees a lot more
    #    DD/MM in its training data from this market.
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %y",
        "%d %b %y",
    ):
        try:
            return _clamp_to_present(datetime.strptime(s, fmt))
        except ValueError:
            continue

    # 3) Relative phrases — best-effort, "today" / "tomorrow" / "in N days/weeks"
    parsed = _try_relative(s)
    if parsed is not None:
        return _clamp_to_present(parsed)

    # 4) Day + short month name without year ("15 Mar") — assume current year
    for fmt in ("%d %b", "%d %B"):
        try:
            parsed = datetime.strptime(s, fmt)
            # Be explicit about the year — Python 3.12+ deprecated
            # letting strptime() default to the current year.
            return _clamp_to_present(parsed.replace(year=datetime.now().year))
        except ValueError:
            continue

    logger.warning("parse_bill_due_date: could not parse %r, falling back to now()", raw)
    return _now()


# ── Internals ────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now()


def _clamp_to_present(dt: datetime) -> datetime:
    """If the parsed date is in the past, treat it as an OCR mistake
    and use `now()`. A bill with a past `due_date` would always be
    flagged `pay_now` by the agent and immediately try to debit the
    wallet — usually the wrong action for a typo'd date.

    Also strips the tzinfo: the rest of the app uses naive datetimes
    (the `bills.due_date` column is `TIMESTAMP WITHOUT TIME ZONE`),
    so a tz-aware parsed value would break arithmetic with `datetime.now()`.
    """
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    if dt < datetime.now():
        logger.warning("parse_bill_due_date: %s is in the past, clamping to now()", dt)
        return _now()
    return dt


def _try_iso(s: str) -> Optional[datetime]:
    """ISO 8601 — full datetime, date-only, with/without TZ, with/without microseconds."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # fromisoformat handles "2026-03-15", "2026-03-15T10:30:00",
    # "2026-03-15T10:30:00+00:00", and PEP-695 cases.
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


_RELATIVE_RE = re.compile(
    r"^\s*"
    r"(?:in\s+)?"  # "in 2 weeks" or "2 weeks from today"
    r"(?P<amount>\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s+"
    r"(?P<unit>day|days|week|weeks|month|months)"
    r"(?:\s+from\s+(?P<base>today|now))?"
    r"\s*\.?"  # tolerate trailing period
    r"\s*$",
    re.IGNORECASE,
)

_WORD_TO_INT = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _try_relative(s: str) -> Optional[datetime]:
    """Parse 'today', 'tomorrow', 'in 2 weeks', '5 days from today', etc."""
    s_low = s.lower().strip().rstrip(".")
    if s_low in ("today", "now"):
        return _now()
    if s_low == "tomorrow":
        return _now() + timedelta(days=1)
    if s_low == "yesterday":
        return _now()  # we clamp past dates anyway
    if s_low in ("next week", "next month"):
        days = 7 if "week" in s_low else 30
        return _now() + timedelta(days=days)

    m = _RELATIVE_RE.match(s)
    if not m:
        return None
    amt_raw = m.group("amount")
    unit = m.group("unit").lower()
    amt = int(amt_raw) if amt_raw.isdigit() else _WORD_TO_INT.get(amt_raw.lower(), 1)
    if unit.startswith("day"):
        return _now() + timedelta(days=amt)
    if unit.startswith("week"):
        return _now() + timedelta(weeks=amt)
    if unit.startswith("month"):
        return _now() + timedelta(days=30 * amt)
    return None
