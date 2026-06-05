"""Telegram bot conversation handlers.

The bot is built and started in `app.services.telegram` and
registered on the FastAPI lifespan. Handlers live here:

  * `auth`         — /start, /link, /unlink, /wallet, /bills, /help
  * `bill_conversation` — text/photo/PDF bill upload flow with
                          Confirm → Edit → Final-Confirm → Payout
  * `helpers`      — shared utilities (DB lookups, keyboards, formatters)

Account-link flow:
  1. User signs up on the web dashboard.
  2. Web dashboard POSTs /api/v1/telegram/link-code → 6-char code in
     `telegram_link_codes` table.
  3. User sends `/link CODE` to the bot.
  4. `link_command` resolves the code, marks the user as linked,
     stores the chat id on the user row.
"""
from app.handlers import auth, bill_conversation, helpers

__all__ = ["auth", "bill_conversation", "helpers"]
  