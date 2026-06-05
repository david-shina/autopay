"""Unit tests for the Telegram bot handlers.

We use PTB's `Application` + `process_update` to drive the handlers
without an actual network. The bot does not need a real token —
we build the Application with a dummy token, dispatch synthetic
`Update` objects, and assert on the bot's `sent_messages` list.

DB-dependent tests reuse the integration conftest's `session`
fixture (Postgres) so we get full SQLAlchemy + JSONB support.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.database import session_scope
from app.core.security import hash_password
from app.models.telegram_link_code import TelegramLinkCode
from app.models.user import User


# ── Helpers ─────────────────────────────────────────────────────────


class _FakeMessage:
    def __init__(self, chat_id: int, text: str | None = None) -> None:
        self.chat_id = chat_id
        self.text = text
        self.replies: list[str] = []
        self.edits: list[str] = []
        self.photo = None
        self.document = None

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append(text)

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append(text)


class _FakeContext:
    def __init__(self, chat_id: int) -> None:
        self.args: list[str] = []
        self.user_data: dict[str, Any] = {}
        self.bot = type(
            "Bot",
            (),
            {
                "send_message": AsyncMock(return_value=_FakeMessage(chat_id)),
                "get_file": AsyncMock(),
            },
        )()


def _link_user_sync(session, *, email: str, chat_id: int | None = None) -> User:
    user = User(
        email=email,
        hashed_password=hash_password("Secret123"),
        first_name="TG",
        last_name="Tester",
        phone_number="0809" + email[:8].rjust(8, "0"),
        telegram_chat_id=str(chat_id) if chat_id is not None else None,
        is_telegram_linked=chat_id is not None,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── /start ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_sends_welcome_message() -> None:
    from app.handlers.auth import start_command
    msg = _FakeMessage(chat_id=12345)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": 12345})()}
    )()
    await start_command(update, _FakeContext(12345))
    assert any("Welcome" in r for r in msg.replies)


# ── /link ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_with_no_args_asks_for_code() -> None:
    from app.handlers.auth import link_command
    msg = _FakeMessage(chat_id=99999)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": 99999})()}
    )()
    ctx = _FakeContext(99999)
    await link_command(update, ctx)
    assert any("include your linking code" in r.lower() for r in msg.replies)


@pytest.mark.asyncio
async def test_link_with_invalid_code_rejects() -> None:
    from app.handlers.auth import link_command
    msg = _FakeMessage(chat_id=99998)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": 99998})()}
    )()
    ctx = _FakeContext(99998)
    ctx.args = ["NOPE12"]
    await link_command(update, ctx)
    assert any("invalid" in r.lower() for r in msg.replies)


@pytest.mark.asyncio
async def test_link_with_valid_code_links_user(session) -> None:
    from app.handlers.auth import link_command
    user = _link_user_sync(session, email="linkme@x.com")
    user_id = user.id  # capture before session closes
    code = TelegramLinkCode.generate_code()
    session.add(
        TelegramLinkCode(
            user_id=user_id,
            code=code,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=15),
        )
    )
    session.commit()

    chat_id = 88888
    msg = _FakeMessage(chat_id=chat_id)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": chat_id})()}
    )()
    ctx = _FakeContext(chat_id)
    ctx.args = [code]
    await link_command(update, ctx)
    assert any("linked" in r.lower() for r in msg.replies)

    with session_scope() as verify:
        refreshed = verify.get(User, user_id)
        assert refreshed is not None
        assert refreshed.is_telegram_linked is True
        assert refreshed.telegram_chat_id == str(chat_id)


@pytest.mark.asyncio
async def test_link_with_expired_code_rejects(session) -> None:
    from app.handlers.auth import link_command
    user = _link_user_sync(session, email="expired@x.com")
    user_id = user.id
    code = TelegramLinkCode.generate_code()
    session.add(
        TelegramLinkCode(
            user_id=user_id,
            code=code,
            expires_at=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
        )
    )
    session.commit()

    msg = _FakeMessage(chat_id=77777)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": 77777})()}
    )()
    ctx = _FakeContext(77777)
    ctx.args = [code]
    await link_command(update, ctx)
    assert any("expired" in r.lower() for r in msg.replies)


# ── /unlink ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unlink_removes_chat_id(session) -> None:
    from app.handlers.auth import unlink_command
    user = _link_user_sync(session, email="unlink@x.com", chat_id=66666)
    user_id = user.id
    msg = _FakeMessage(chat_id=66666)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": 66666})()}
    )()
    ctx = _FakeContext(66666)
    await unlink_command(update, ctx)

    with session_scope() as verify:
        refreshed = verify.get(User, user_id)
        assert refreshed is not None
        assert refreshed.is_telegram_linked is False
        assert refreshed.telegram_chat_id is None


# ── /wallet ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wallet_unlinked_asks_to_link() -> None:
    from app.handlers.auth import wallet_command
    msg = _FakeMessage(chat_id=55555)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": 55555})()}
    )()
    await wallet_command(update, _FakeContext(55555))
    assert any("link" in r.lower() for r in msg.replies)


@pytest.mark.asyncio
async def test_wallet_with_no_va_warns_user(session) -> None:
    from app.handlers.auth import wallet_command
    _link_user_sync(session, email="nowallet@x.com", chat_id=44444)
    msg = _FakeMessage(chat_id=44444)
    update = type(
        "U", (), {"message": msg, "effective_chat": type("C", (), {"id": 44444})()}
    )()
    await wallet_command(update, _FakeContext(44444))
    assert any(
        "no virtual account" in r.lower() or "wallet" in r.lower()
        for r in msg.replies
    )


# ── Pure-function helpers ───────────────────────────────────────────


def test_get_linked_user_returns_none_for_unlinked() -> None:
    from app.handlers.helpers import get_linked_user
    assert get_linked_user("999999") is None


def test_get_linked_user_returns_user_for_linked(session) -> None:
    _link_user_sync(session, email="helper@x.com", chat_id=33333)
    from app.handlers.helpers import get_linked_user
    user = get_linked_user("33333")
    assert user is not None
    user_id = user.id
    with session_scope() as verify:
        refreshed = verify.get(User, user_id)
        assert refreshed is not None
        assert refreshed.email == "helper@x.com"


def test_escape_md_handles_reserved_chars() -> None:
    from app.handlers.helpers import escape_md
    assert escape_md("foo_bar") == "foo\\_bar"
    assert escape_md("a*b*c") == "a\\*b\\*c"
    assert escape_md("[link](http://x.com)") == "\\[link](http://x.com)"
    assert escape_md("") == ""


def test_parse_user_date_accepts_common_formats() -> None:
    from app.handlers.helpers import parse_user_date
    assert parse_user_date("2026-12-31") is not None
    assert parse_user_date("31 Dec 2026") is not None
    assert parse_user_date("December 31, 2026") is not None
    assert parse_user_date("garbage") is None
