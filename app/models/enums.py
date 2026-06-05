"""Shared enums for status fields.

These mirror the CHECK constraints in schema.sql. Python enums are the
canonical representation in code; the database enforces the same set
via constraints.
"""
from enum import Enum


class TransactionType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class TransactionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    REVERSED = "reversed"


class BillStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditActor(str, Enum):
    USER = "user"
    SYSTEM = "system"
    WEBHOOK = "webhook"
    SCHEDULER = "scheduler"
    ADMIN = "admin"


class AuditEventType(str, Enum):
    # User lifecycle
    USER_SIGNUP = "user.signup"
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    USER_TELEGRAM_LINKED = "user.telegram_linked"

    # Wallet
    WALLET_CREDITED = "wallet.credited"
    WALLET_DEBITED = "wallet.debited"
    WALLET_REFUND = "wallet.refund"

    # Payouts
    PAYOUT_ATTEMPTED = "payout.attempted"
    PAYOUT_SUCCEEDED = "payout.succeeded"
    PAYOUT_FAILED = "payout.failed"
    PAYOUT_REVERSED = "payout.reversed"

    # Bills
    BILL_CREATED = "bill.created"
    BILL_SCHEDULED = "bill.scheduled"
    BILL_PAID = "bill.paid"
    BILL_FAILED = "bill.failed"
    BILL_CANCELLED = "bill.cancelled"
    BILL_RECURRENCE_CREATED = "bill.recurrence_created"

    # Virtual accounts
    VA_CREATED = "va.created"
    VA_CREDITED = "va.credited"

    # KYC
    KYC_BVN_SUBMITTED = "kyc.bvn_submitted"
    KYC_BVN_VALIDATED = "kyc.bvn_validated"

    # Webhooks
    WEBHOOK_RECEIVED = "webhook.received"
    WEBHOOK_REPLAY = "webhook.replay"
    WEBHOOK_UNKNOWN = "webhook.unknown"


class AuditEntityType(str, Enum):
    """Closed set of `entity_type` values written to audit_logs.

    Why a Python enum? The `entity_type` column is a polymorphic pointer
    (paired with `entity_id`) to a row in some other table. A typo at
    write time ("Bil" vs "bill") would silently break the
    "show audit trail for bill #42" query. This enum is enforced at
    every helper that calls `write_audit()`, so the only places a typo
    can creep in are *new* helper functions, which is easy to catch in
    code review.
    """

    USER = "user"
    KYC_RECORD = "kyc_record"
    VIRTUAL_ACCOUNT = "virtual_account"
    BILL = "bill"
    TRANSACTION = "transaction"
    REFRESH_TOKEN = "refresh_token"
