"""SQLModel ORM models.

Importing this package registers every model on `SQLModel.metadata`,
which is what Alembic autogenerate + `init_db()` use.
"""
from app.models.audit_log import AuditLog
from app.models.bill import Bill
from app.models.enums import AuditEntityType
from app.models.kyc import KycRecord
from app.models.refresh_token import RefreshToken
from app.models.telegram_link_code import TelegramLinkCode
from app.models.transaction import Transaction
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.models.webhook_event import WebhookEvent

__all__ = [
    "AuditEntityType",
    "AuditLog",
    "Bill",
    "KycRecord",
    "RefreshToken",
    "TelegramLinkCode",
    "Transaction",
    "User",
    "VirtualAccount",
    "WebhookEvent",
]
