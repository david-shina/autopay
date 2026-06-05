"""Bill upload conversation handler.

Flow:
  1. User sends a bill (text, photo, or PDF).
  2. Loader extracts fields; we show a summary with Confirm / Edit / Cancel.
  3. On Confirm: we run the decision agent.
  4. pay_now → confirm-with-amount; schedule → "I'll process it when due";
     hold → "Top up your wallet".

This is a port of the original MVP's bill_conversation, retargeted at
the new services layer (payout.execute_payout, loaders.TextLoader,
agents.run_agent).
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.agents.graphs import run_agent
from app.agents.state import Decision
from app.core.config import settings
from app.core.database import session_scope
from app.handlers.helpers import (
    EDITABLE_FIELDS,
    confirm_keyboard,
    escape_md,
    field_keyboard,
    final_keyboard,
    format_bill_summary,
    get_linked_user,
    parse_user_date,
)
from app.models.bill import Bill
from app.models.enums import AuditActor, AuditEntityType, AuditEventType, BillStatus
from app.models.user import User
from app.services.audit import audit_bill_created
from app.services.date_parser import parse_bill_due_date
from app.services.loaders import loader_from_upload
from app.services.payments import PaymentProvider, get_payment_provider
from app.services.payout import execute_payout

logger = logging.getLogger(__name__)


# Conversation states
CONFIRM, CHOOSE_FIELD, EDIT_VALUE, FINAL_CONFIRM = range(4)


# ── Step 1: receive bill ────────────────────────────────────────────

async def receive_bill(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    msg = update.message
    chat_id = str(update.effective_chat.id)

    user = get_linked_user(chat_id)
    if user is None:
        await msg.reply_text(
            "🔒 *Account not linked.*\n\nSend `/link YOUR_CODE` to connect your account.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # Build the loader based on what was sent.
    try:
        loader = await _build_loader(msg, context)
    except ValueError as exc:
        await msg.reply_text(f"⚠️ {exc}")
        return ConversationHandler.END

    if loader is None:
        # Not a bill at all (e.g. a sticker).
        return ConversationHandler.END

    await msg.reply_text("⏳ Extracting bill details...")

    try:
        extracted = await loader.extract()
    except Exception as exc:  # noqa: BLE001
        logger.warning("bill extraction failed: %s", exc)
        await msg.reply_text(
            f"❌ Couldn't extract bill details: {exc}\n\n"
            "Try a clearer photo or paste the text manually."
        )
        return ConversationHandler.END

    if not extracted.vendor_name or float(extracted.amount) <= 0:
        await msg.reply_text(
            "❌ I could read the file but couldn't find a vendor and amount.\n\n"
            "Send the bill as text instead, e.g.:\n"
            "`Pay DSTV 5000 by Friday 0123456789 GTBank 058`"
        )
        return ConversationHandler.END

    # Stash for downstream steps
    context.user_data["bill"] = {
        "vendor_name": extracted.vendor_name,
        "amount": float(extracted.amount),
        "currency": extracted.currency or "NGN",
        "due_date": extracted.due_date or "",
        "account_number": extracted.account_number or "",
        "bank_code": extracted.bank_code or "",
    }
    context.user_data["user_id"] = user.id
    context.user_data["user_balance"] = float(user.balance)

    await msg.reply_text(
        format_bill_summary(context.user_data["bill"]),
        parse_mode="Markdown",
        reply_markup=confirm_keyboard(),
    )
    return CONFIRM


async def _build_loader(msg, context: ContextTypes.DEFAULT_TYPE):
    """Pick a loader based on what the user sent. Returns None if
    this isn't a bill (e.g. a plain non-text message)."""
    if msg.text and not msg.document and not msg.photo:
        if msg.text.startswith("/"):
            return None  # probably a /command
        from app.services.loaders import TextLoader
        return TextLoader(msg.text)

    if msg.photo:
        # Use the largest photo (last in the array).
        photo = msg.photo[-1]
        file_info = await context.bot.get_file(photo.file_id)
        data = bytes(await file_info.download_as_bytearray())
        from app.services.loaders import ImageLoader
        return ImageLoader(data, mime_type="image/jpeg")

    if msg.document:
        mime = (msg.document.mime_type or "").lower()
        if "pdf" not in mime:
            raise ValueError("I can only process PDF documents. Send a photo for paper bills.")
        file_info = await context.bot.get_file(msg.document.file_id)
        data = bytes(await file_info.download_as_bytearray())
        from app.services.loaders import PDFLoader
        return PDFLoader(data)

    return None


# ── Step 2a: confirm → run agent ────────────────────────────────────

async def handle_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🤖 Analysing payment...")

    bill_data = context.user_data.get("bill", {})
    user_id = context.user_data.get("user_id")
    user_balance = Decimal(str(context.user_data.get("user_balance", "0")))

    if not user_id or not bill_data:
        await query.edit_message_text(
            "Session expired. Send the bill again."
        )
        return ConversationHandler.END

    # Persist the bill first so the agent has a bill_id to refer to.
    with session_scope() as session:
        bill = Bill(
            user_id=user_id,
            vendor_name=bill_data["vendor_name"],
            amount=Decimal(str(bill_data["amount"])),
            currency=bill_data.get("currency", "NGN"),
            due_date=parse_bill_due_date(bill_data.get("due_date")),
            account_number=bill_data.get("account_number") or None,
            bank_code=bill_data.get("bank_code") or None,
            status=BillStatus.PENDING.value,
        )
        session.add(bill)
        session.flush()
        bill_id = bill.id
        audit_bill_created(
            session,
            user_id=user_id,
            bill_id=bill_id,
            amount=float(bill.amount),
            provider="paystack",
        )

    days_until_due = (bill.due_date - datetime.now()).days
    decision = run_agent(
        user_balance=user_balance,
        bill_amount=Decimal(str(bill.amount)),
        fee=Decimal(str(settings.payout_fee_ngn)),
        days_until_due=days_until_due,
    )

    context.user_data["bill_id"] = bill_id

    if decision.decision == Decision.PAY_NOW:
        fee = Decimal(str(settings.payout_fee_ngn))
        total = Decimal(str(bill.amount)) + fee
        await query.edit_message_text(
            f"🤖 *Agent says: Pay Now*\n"
            f"_{decision.reason}_\n\n"
            f"💳 *Payment Summary*\n"
            f"Vendor: *{escape_md(bill_data['vendor_name'])}*\n"
            f"Account: `{escape_md(bill_data.get('account_number', 'N/A'))}`\n\n"
            f"Amount:  ₦{float(bill.amount):,.2f}\n"
            f"Fee:     ₦{float(fee):,.2f}\n"
            f"Total:   ₦{float(total):,.2f}\n"
            f"Balance after: ₦{float(user_balance - total):,.2f}\n\n"
            f"Do you want to proceed?",
            parse_mode="Markdown",
            reply_markup=final_keyboard(),
        )
        return FINAL_CONFIRM

    if decision.decision == Decision.SCHEDULE:
        with session_scope() as session:
            bill = session.get(Bill, bill_id)
            if bill:
                bill.status = BillStatus.SCHEDULED.value
                session.add(bill)
        await query.edit_message_text(
            f"🤖 *Agent says: Schedule*\n"
            f"_{decision.reason}_\n\n"
            f"🗓 *Payment Scheduled*\n\n"
            f"₦{float(bill.amount):,.2f} → {escape_md(bill_data['vendor_name'])}\n"
            f"Due: {bill.due_date.date().isoformat()}\n\n"
            f"I'll process it automatically when it's due.",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Decision.HOLD
    fee = Decimal(str(settings.payout_fee_ngn))
    total = Decimal(str(bill.amount)) + fee
    await query.edit_message_text(
        f"🤖 *Agent says: Hold*\n"
        f"_{decision.reason}_\n\n"
        f"⏸ *Payment on Hold*\n\n"
        f"Bill: ₦{float(bill.amount):,.2f} to {escape_md(bill_data['vendor_name'])}\n"
        f"Your Balance: ₦{float(user_balance):,.2f}\n"
        f"Shortfall: ₦{float(max(Decimal(0), total - user_balance)):,.2f}\n\n"
        f"Top up your wallet with `/wallet` and send the bill again.",
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


# ── Step 2b: edit → field picker ───────────────────────────────────

async def handle_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Which field would you like to correct?",
        reply_markup=field_keyboard(),
    )
    return CHOOSE_FIELD


# ── Step 2c: cancel ────────────────────────────────────────────────

async def handle_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Bill cancelled. Send another bill whenever you're ready.")
    # Clean up the persisted bill if any
    bill_id = context.user_data.get("bill_id")
    if bill_id:
        with session_scope() as session:
            bill = session.get(Bill, bill_id)
            if bill and bill.status in (BillStatus.PENDING.value, BillStatus.SCHEDULED.value):
                bill.status = BillStatus.CANCELLED.value
                session.add(bill)
    context.user_data.clear()
    return ConversationHandler.END


# ── Step 3: field chosen → prompt for new value ────────────────────

async def handle_field_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back":
        await query.edit_message_text(
            format_bill_summary(context.user_data["bill"]),
            parse_mode="Markdown",
            reply_markup=confirm_keyboard(),
        )
        return CONFIRM

    field_key = query.data.replace("field:", "")
    context.user_data["editing_field"] = field_key
    current = context.user_data["bill"].get(field_key, "N/A")
    await query.edit_message_text(
        f"✏️ Editing *{EDITABLE_FIELDS[field_key]}*\n"
        f"Current value: `{escape_md(str(current))}`\n\n"
        "Type the new value:",
        parse_mode="Markdown",
    )
    return EDIT_VALUE


# ── Step 4: new value typed → update and return to confirm ─────────

async def handle_new_value(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    new_value = update.message.text.strip()
    field_key = context.user_data.get("editing_field")
    field_label = EDITABLE_FIELDS.get(field_key, field_key)

    if field_key == "amount":
        try:
            new_value = float(new_value.replace(",", ""))
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid amount. Try again (e.g. `15000.00`):",
                parse_mode="Markdown",
            )
            return EDIT_VALUE
    elif field_key == "due_date":
        parsed = parse_user_date(new_value)
        if parsed is None:
            await update.message.reply_text(
                "⚠️ I couldn't parse that date. Try `2026-12-31` or `31 Dec 2026`:",
                parse_mode="Markdown",
            )
            return EDIT_VALUE
        new_value = parsed.isoformat()

    context.user_data["bill"][field_key] = new_value

    await update.message.reply_text(
        f"✅ *{field_label}* updated\n\n"
        + format_bill_summary(context.user_data["bill"]),
        parse_mode="Markdown",
        reply_markup=confirm_keyboard(),
    )
    return CONFIRM


# ── Step 5: final confirm → execute payout ─────────────────────────

async def handle_final_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    bill_id = context.user_data.get("bill_id")
    bill_data = context.user_data.get("bill", {})
    user_balance = Decimal(str(context.user_data.get("user_balance", "0")))

    if not bill_id:
        await query.edit_message_text("Session expired. Send the bill again.")
        return ConversationHandler.END

    await query.edit_message_text("⏳ Processing payment...")

    provider: PaymentProvider = get_payment_provider()
    try:
        with session_scope() as session:
            result = await execute_payout(session, bill_id=bill_id, provider=provider)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("payout failed for bill %d: %s", bill_id, exc)
        await query.edit_message_text(
            f"❌ *Payment Failed*\n\nReason: {exc}\n\nPlease try again or top up your wallet.",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END

    fee = Decimal(str(settings.payout_fee_ngn))
    total = Decimal(str(bill_data.get("amount", 0))) + fee
    await query.edit_message_text(
        f"✅ *Payment Initiated*\n\n"
        f"₦{float(bill_data.get('amount', 0)):,.2f} → "
        f"*{escape_md(bill_data.get('vendor_name', ''))}*\n"
        f"Reference: `{result.message}`\n"
        f"Remaining balance: ₦{float(user_balance - total):,.2f}\n\n"
        f"You'll get a notification when the transfer completes.",
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


# ── Cancel command fallback ────────────────────────────────────────

async def cancel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send a new bill whenever you're ready.")
    return ConversationHandler.END


# ── ConversationHandler factory ────────────────────────────────────

def build_bill_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT | filters.PHOTO | filters.Document.ALL,
                receive_bill,
            )
        ],
        states={
            CONFIRM: [
                CallbackQueryHandler(handle_confirm, pattern="^confirm$"),
                CallbackQueryHandler(handle_edit, pattern="^edit$"),
                CallbackQueryHandler(handle_cancel, pattern="^cancel$"),
            ],
            CHOOSE_FIELD: [
                CallbackQueryHandler(handle_field_choice, pattern="^(field:.+|back)$"),
            ],
            EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_value),
            ],
            FINAL_CONFIRM: [
                CallbackQueryHandler(handle_final_confirm, pattern="^final_confirm$"),
                CallbackQueryHandler(handle_cancel, pattern="^final_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_user=True,
        per_chat=True,
    )
