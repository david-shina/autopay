"""Telegram bot setup + lifecycle.

The bot runs as a background task inside the FastAPI app's lifespan.
We support two modes:

  * **Webhook mode** (production): set `WEBHOOK_URL` and the app
    exposes `POST /telegram/webhook` to receive Telegram updates.
  * **Polling mode** (dev): if `WEBHOOK_URL` is empty, the bot
    long-polls Telegram. The FastAPI process owns the poller.

Either way, only one mode runs at a time. The choice is governed by
the `webhook_url` setting; an empty string means polling.

Tests can build a bot without starting it via `build_application()`
and dispatch updates via PTB's `application.process_update(update)`.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
)

from app.core.config import settings
from app.handlers.auth import (
    bills_command,
    help_command,
    link_command,
    start_command,
    unlink_command,
    wallet_command,
)
from app.handlers.bill_conversation import build_bill_conversation

logger = logging.getLogger(__name__)

_application: Optional[Application] = None


# ── Application factory ─────────────────────────────────────────────

def build_application(token: str) -> Application:
    """Build a `python-telegram-bot` Application and register all
    handlers. Does NOT start it — the caller decides webhook vs polling."""
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set; cannot build the bot. "
            "Set it in .env or skip bot setup by leaving it empty."
        )

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("link", link_command))
    app.add_handler(CommandHandler("unlink", unlink_command))
    app.add_handler(CommandHandler("wallet", wallet_command))
    app.add_handler(CommandHandler("bills", bills_command))
    app.add_handler(build_bill_conversation())

    # Last-resort error handler — logs but does not crash the bot.
    async def _on_error(update: object, context: CallbackContext) -> None:
        logger.exception("Unhandled exception in bot", exc_info=context.error)

    app.add_error_handler(_on_error)
    return app


# ── Lifecycle (used from app/main.py:lifespan) ──────────────────────

async def start_bot() -> Optional[Application]:
    """Initialize and start the bot in webhook or polling mode.
    Returns the Application, or None if there's no token configured.
    """
    global _application
    token = settings.telegram_bot_token
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN unset; Telegram bot disabled.")
        return None

    _application = build_application(token)
    await _application.initialize()
    await _application.start()

    if settings.webhook_url:
        # Webhook mode: Telegram pushes updates to settings.webhook_url.
        # We don't `run_until_complete` here; updates are received via
        # the /telegram/webhook FastAPI route.
        await _application.bot.set_webhook(url=settings.webhook_url)
        logger.info("Telegram bot running in webhook mode → %s", settings.webhook_url)
    else:
        # Polling mode: long-poll Telegram for updates. This runs
        # in the background as part of the bot's start, so we don't
        # block the FastAPI event loop.
        await _application.updater.start_polling()
        logger.info("Telegram bot running in polling mode (no WEBHOOK_URL set).")

    return _application


async def stop_bot() -> None:   
    """Tear down the bot on FastAPI shutdown."""
    global _application
    if _application is None:
        return
    try:
        if _application.updater and _application.updater.running:
            await _application.updater.stop()
        if settings.webhook_url:
            try:
                await _application.bot.delete_webhook()
            except Exception as exc:  # noqa: BLE001
                logger.warning("delete_webhook failed: %s", exc)
        await _application.stop()
        await _application.shutdown()
    finally:
        _application = None


# ── Webhook route ───────────────────────────────────────────────────

webhook_router = APIRouter(tags=["telegram"])


@webhook_router.post(
    "/telegram/webhook",
    summary="Receive Telegram updates (webhook mode only)",
)
async def telegram_webhook(request: Request) -> dict:
    if _application is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bot is not running.",
        )
    try:
        payload = await request.json()
        update = Update.de_json(payload, _application.bot)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bad update payload: {exc}",
        ) from exc

    await _application.process_update(update)
    return {"ok": True}


def get_application() -> Optional[Application]:
    """Test/diagnostic accessor for the running application."""
    return _application
