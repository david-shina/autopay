"""Shared helpers for the Telegram bot handlers.

`get_linked_user(chat_id)` is the single point where a Telegram chat
ID gets resolved to a `User` row. Every conversation handler calls
this on entry; the unauthenticated case returns None and the handler
tells the user to run /link.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.database import session_scope
from app.models.user import User
from app.models.virtual_account import VirtualAccount


# ── Editable fields the user can correct on the extracted bill ──────

EDITABLE_FIELDS = {
    "vendor_name": "Vendor name",
    "amount": "Amount",
    "due_date": "Due date",
    "account_number": "Account number",
    "bank_code": "Bank code",
}


# ── Keyboards ───────────────────────────────────────────────────────

def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
            InlineKeyboardButton("✏️ Edit", callback_data="edit"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])


def field_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"field:{key}")]
        for key, label in EDITABLE_FIELDS.items()
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def final_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Pay now", callback_data="final_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="final_cancel"),
        ],
    ])


# ── Formatters ──────────────────────────────────────────────────────

def format_bill_summary(data: dict) -> str:
    amount = data.get("amount")
    try:
        amount_str = f"{float(amount):,.2f}" if amount is not None else "N/A"
    except (TypeError, ValueError):
        amount_str = str(amount)
    return (
        "📄 *Extracted Bill Details*\n\n"
        f"🏢 Vendor: `{data.get('vendor_name', 'N/A')}`\n"
        f"💰 Amount: `{amount_str} {data.get('currency', 'NGN')}`\n"
        f"📅 Due date: `{data.get('due_date', 'N/A')}`\n"
        f"🏦 Account: `{data.get('account_number', 'N/A')}`\n"
        f"🔢 Bank code: `{data.get('bank_code', 'N/A')}`\n\n"
        "Is this correct?"
    )


# ── Auth helper ─────────────────────────────────────────────────────

def get_linked_user(chat_id: str | int) -> Optional[User]:
    """Look up the user linked to a Telegram chat id. Returns None if
    the chat hasn't been linked yet.

    Eager-loads common fields (id, balance, telegram_chat_id,
    is_telegram_linked, first_name, last_name, email) so the returned
    object is usable after the session closes — otherwise
    SQLAlchemy would raise DetachedInstanceError on first attribute
    access.
    """
    from sqlalchemy import select as _sa_select
    with session_scope() as session:
        user = session.exec(
            _sa_select(User).where(
                User.telegram_chat_id == str(chat_id),
                User.is_telegram_linked == True,  # noqa: E712
            )
        ).scalar_one_or_none()
        if user is not None:
            # Force-load the fields the handlers care about.
            _ = (user.id, user.balance, user.currency, user.telegram_chat_id,
                 user.first_name, user.last_name, user.email)
        return user


def get_user_va(user: User) -> Optional[VirtualAccount]:
    from sqlalchemy import select as _sa_select
    with session_scope() as session:
        return session.exec(
            _sa_select(VirtualAccount).where(VirtualAccount.user_id == user.id)
        ).scalar_one_or_none()


# ── Date parsing (bot-side) ─────────────────────────────────────────

def parse_user_date(text: str) -> Optional[datetime]:
    """Try a few common formats. Returns naive datetime on success.
    The bot only needs to read user input; the API's date_parser has
    the full LLM-aware logic."""
    from dateutil import parser as dateparser
    try:
        dt = dateparser.parse(text, fuzzy=True)
        return dt.replace(tzinfo=None) if dt else None
    except (ValueError, TypeError, OverflowError):
        return None


# ── Markdown escape ─────────────────────────────────────────────────

def escape_md(text: str) -> str:
    """Escape Telegram Markdown V1 reserved chars. Used whenever we
    interpolate user-supplied data into a Markdown message."""
    if not text:
        return ""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


__all__ = [
    "EDITABLE_FIELDS",
    "confirm_keyboard",
    "field_keyboard",
    "final_keyboard",
    "format_bill_summary",
    "get_linked_user",
    "get_user_va",
    "parse_user_date",
    "escape_md",
]
