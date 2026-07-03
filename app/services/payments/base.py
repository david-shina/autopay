"""Payment provider abstraction.

We never want to lock the business logic to a single gateway. The
`PaymentProvider` Protocol below is the only interface business code
should depend on. Concrete implementations live alongside this file
(`paystack.py`, etc.).

DTOs are kept as `dataclass(frozen=True)` (not Pydantic) because:
  * they are pure data crossing an internal boundary, no validation
    needed beyond typing;
  * they must be cheap to construct in tests;
  * they are returned by *the provider* and validated by the caller,
    so the provider never has to know our app's request validation
    rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Optional, Protocol, runtime_checkable


# ── DTOs ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VirtualAccountData:
    """A dedicated virtual account (DVA) issued to a user by the provider.

    `provider_reference` is the gateway's ID for this DVA (e.g. Paystack
    `dedicated_account_id`). It is what we store on `virtual_accounts`
    as the FK to gateway reality.
    """

    account_number: str
    account_name: str
    bank_name: str
    bank_code: str
    provider_reference: str  # gateway-side ID
    provider: str  # "paystack", "flutterwave", ...


@dataclass(frozen=True)
class ResolvedAccount:
    """Result of "look up the name behind this account number"."""

    account_number: str
    account_name: str
    bank_code: str


@dataclass(frozen=True)
class TransferResult:
    """The provider's response when we initiated a transfer (payout)."""

    provider_reference: str  # our reference that the provider echoed back
    provider_transfer_id: str  # gateway's transfer ID
    status: str  # "pending" | "success" | "failed" | "reversed"
    raw_response: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WebhookEvent:
    """A verified webhook from the provider.

    `provider_reference` ties the event back to our own records
    (transaction.provider_reference, bill.provider_reference, etc.).
    `event_type` is normalized to a small closed set so business code
    can switch on it safely.
    `event_id` is a stable id from the provider used to dedup retries —
    either the provider's `event.id` or a SHA-256 of the raw body when
    the provider omits the field.
    """

    event_type: str  # "charge.success" | "transfer.success" | "transfer.failed" | "transfer.reversed" | "dedicatedaccount.assign.success"
    provider_reference: str
    event_id: str  # provider's event.id, or a body hash for dedup
    amount_kobo: Optional[int] = None
    raw: dict = field(default_factory=dict)


# ── Protocol ────────────────────────────────────────────────────────

@runtime_checkable
class PaymentProvider(Protocol):
    """The contract every payment-gateway implementation must satisfy."""

    name: str  # "paystack" | "flutterwave" | ...

    async def create_customer(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone: Optional[str] = None,
    ) -> str:
        """Create a customer at the provider; return provider's customer_id/code."""
        ...

    async def create_virtual_account(
        self,
        *,
        customer_code: str,
        preferred_bank: Optional[str] = None,
        account_name: Optional[str] = None,
    ) -> VirtualAccountData:
        """Issue a dedicated virtual account for `customer_code`.

        `account_name` is required by providers with no separate
        customer object to draw a name from (e.g. Nomba, where the
        account holder name is passed at creation time). Providers
        that already know the name via `customer_code` (e.g. Paystack)
        may ignore it.
        """
        ...

    async def resolve_account(
        self,
        *,
        account_number: str,
        bank_code: str,
    ) -> ResolvedAccount:
        """Look up the name on `account_number` at `bank_code`."""
        ...

    async def create_transfer_recipient(
        self,
        *,
        account_number: str,
        bank_code: str,
        account_name: str,
    ) -> str:
        """Create a transfer recipient; return provider's recipient_code.

        Providers with no server-side recipient concept (e.g. Nomba)
        may treat this as a no-op and return a placeholder string --
        `initiate_transfer` receives the account details directly too.
        """
        ...

    async def initiate_transfer(
        self,
        *,
        amount_kobo: int,
        recipient_code: str,
        reference: str,
        reason: str,
        account_number: Optional[str] = None,
        bank_code: Optional[str] = None,
        account_name: Optional[str] = None,
    ) -> TransferResult:
        """Move `amount_kobo` (1 NGN = 100 kobo) from our balance to recipient.

        `account_number`/`bank_code`/`account_name` are required by
        providers that transfer directly to an account rather than a
        pre-created recipient (e.g. Nomba). Providers that only need
        `recipient_code` (e.g. Paystack) may ignore them.
        """
        ...

    def verify_webhook_signature(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> bool:
        """Return True iff `headers` prove `raw_body` is an authentic
        webhook delivery.

        Takes the full header mapping rather than one pre-extracted
        header, since some providers (e.g. Nomba) sign over several
        headers plus fields from the body itself, not a single
        HMAC-of-raw-body header.
        """
        ...

    async def parse_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> WebhookEvent:
        """Verify signature, then parse into a `WebhookEvent`. Raises on bad sig."""
        ...
