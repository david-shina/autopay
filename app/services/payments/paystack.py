"""Paystack implementation of the PaymentProvider protocol.

Reference: https://paystack.com/docs/api/
All amounts are in KOBO (1 NGN = 100 kobo). We pass amounts as `int`
to the API and let Paystack format them. Returning them as `int` to
callers keeps the rest of the codebase in integer-kobo until the
moment we format for the user.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.services.payments.base import (
    PaymentProvider,
    ResolvedAccount,
    TransferResult,
    VirtualAccountData,
    WebhookEvent,
)
from app.services.payments.exceptions import (
    AccountNameMismatch,
    AuthenticationError,
    InvalidAccount,
    KYCRequired,
    PaymentError,
    ProviderError,
    WebhookSignatureError,
)

logger = logging.getLogger(__name__)

PAYSTACK_BASE = "https://api.paystack.co"


# ── Paystack-specific error mapping ─────────────────────────────────

def _map_paystack_error(status_code: int, body: dict) -> PaymentError:
    """Translate a Paystack error JSON into the right typed exception.

    Paystack's `message` is human-readable and inconsistent; we lean on
    the HTTP status code plus a small set of well-known messages
    (`"Invalid key"`, `"Account name mismatch"`, etc.) to pick the
    exception class.
    """
    message = body.get("message") or "Unknown Paystack error"
    raw = body.get("data") or {}

    if status_code in (401, 403) or "Invalid key" in message:
        return AuthenticationError(message, provider="paystack", raw=raw)

    if "Account name" in message and "mismatch" in message.lower():
        return AccountNameMismatch(message, provider="paystack", raw=raw)

    if "Insufficient" in message or "balance" in message.lower():
        # Paystack says "You have insufficient funds" when our merchant
        # balance is too low — rare in practice but possible.
        from app.services.payments.exceptions import InsufficientFunds
        return InsufficientFunds(message, provider="paystack", raw=raw)

    if "KYC" in message or "BVN" in message or "identity" in message.lower():
        return KYCRequired(message, provider="paystack", raw=raw)

    if "resolve" in message.lower() or "invalid account" in message.lower():
        return InvalidAccount(message, provider="paystack", raw=raw)

    return ProviderError(message, provider="paystack", raw=raw)


# ── Provider implementation ─────────────────────────────────────────

class PaystackProvider(PaymentProvider):
    name = "paystack"

    def __init__(self, *, secret_key: str, timeout: float = 30.0) -> None:
        self._secret_key = secret_key
        self._timeout = timeout
        # We open the client lazily so tests that never call a method
        # don't pay the connection-pool cost.
        self._client: Optional[httpx.AsyncClient] = None

    async def _client_lazy(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=PAYSTACK_BASE,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """Make a Paystack call and return the `data` payload.

        Raises a typed `PaymentError` subclass on any non-2xx response.
        Raises `ProviderError` on transport failures.
        """
        client = await self._client_lazy()
        try:
            resp = await client.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Paystack transport error: {exc}", provider="paystack"
            ) from exc

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"Paystack returned non-JSON ({resp.status_code})",
                provider="paystack",
            ) from exc

        if not body.get("status", False):
            raise _map_paystack_error(resp.status_code, body)

        return body.get("data") or {}

    # ── Customer + DVA ──────────────────────────────────────────────

    async def create_customer(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone: Optional[str] = None,
    ) -> str:
        data = await self._request(
            "POST",
            "/customer",
            json_body={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                **({"phone": phone} if phone else {}),
            },
        )
        return str(data["customer_code"])

    async def create_virtual_account(
        self,
        *,
        customer_code: str,
        preferred_bank: Optional[str] = None,
    ) -> VirtualAccountData:
        payload: dict[str, Any] = {"customer": customer_code}
        if preferred_bank:
            payload["preferred_bank"] = preferred_bank

        data = await self._request("POST", "/dedicated_account", json_body=payload)

        return VirtualAccountData(
            account_number=str(data["account_number"]),
            account_name=str(data["account_name"]),
            bank_name=str(data["bank"]["name"]),
            bank_code=str(data["bank"]["id"]),
            provider_reference=str(data["id"]),  # dedicated_account_id
            provider=self.name,
        )

    # ── Account resolution + transfers ──────────────────────────────

    async def resolve_account(
        self,
        *,
        account_number: str,
        bank_code: str,
    ) -> ResolvedAccount:
        data = await self._request(
            "GET",
            "/bank/resolve",
            params={"account_number": account_number, "bank_code": bank_code},
        )
        return ResolvedAccount(
            account_number=str(data["account_number"]),
            account_name=str(data["account_name"]),
            bank_code=str(data["bank_code"]),
        )

    async def create_transfer_recipient(
        self,
        *,
        account_number: str,
        bank_code: str,
        account_name: str,
    ) -> str:
        data = await self._request(
            "POST",
            "/transferrecipient",
            json_body={
                "type": "nuban",
                "name": account_name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": "NGN",
            },
        )
        return str(data["recipient_code"])

    async def initiate_transfer(
        self,
        *,
        amount_kobo: int,
        recipient_code: str,
        reference: str,
        reason: str,
    ) -> TransferResult:
        data = await self._request(
            "POST",
            "/transfer",
            json_body={
                "source": "balance",
                "amount": amount_kobo,
                "recipient": recipient_code,
                "reference": reference,
                "reason": reason,
                "currency": "NGN",
            },
        )
        return TransferResult(
            provider_reference=str(data.get("reference") or reference),
            provider_transfer_id=str(data["id"]),
            status=str(data.get("status") or "pending"),
            raw_response=data,
        )

    # ── Webhook signature + parsing ─────────────────────────────────

    def verify_webhook_signature(
        self,
        *,
        raw_body: bytes,
        signature_header: str,
    ) -> bool:
        if not signature_header:
            return False
        digest = hmac.new(
            self._secret_key.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(digest, signature_header)

    async def parse_webhook(
        self,
        *,
        raw_body: bytes,
        signature_header: str,
    ) -> WebhookEvent:
        if not self.verify_webhook_signature(
            raw_body=raw_body, signature_header=signature_header
        ):
            raise WebhookSignatureError(
                "Invalid Paystack webhook signature", provider="paystack"
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookSignatureError(
                f"Webhook body is not valid JSON: {exc}", provider="paystack"
            ) from exc

        event_name = str(payload.get("event") or "")
        data = payload.get("data") or {}

        # Normalize event names. The Provider's webhook event string
        # is what business code switches on.
        provider_ref = ""
        amount_kobo: Optional[int] = None

        if event_name == "charge.success":
            provider_ref = str(data.get("reference") or "")
            amount_kobo = int(data.get("amount") or 0)
        elif event_name in ("transfer.success", "transfer.failed", "transfer.reversed"):
            provider_ref = str(data.get("reference") or "")
            amount_kobo = int(data.get("amount") or 0)
        elif event_name == "dedicatedaccount.assign.success":
            # Has no useful `reference`; the DVA's `id` is the lookup key.
            provider_ref = str(data.get("id") or "")
        else:
            # Unknown event — pass through with whatever reference we
            # can find, but log loudly so the team can decide.
            logger.warning("Unknown Paystack webhook event: %s", event_name)
            provider_ref = str(data.get("reference") or data.get("id") or "")

        # Stable id for dedup. Paystack's modern payloads have `id` at
        # the top level; older ones don't, so fall back to a SHA-256 of
        # the raw body. The first delivery of any event will compute
        # the same value; subsequent retries will collide on the
        # UNIQUE(provider, event_id) constraint in webhook_events.
        event_id = str(
            payload.get("id")
            or payload.get("event_id")
            or hashlib.sha256(raw_body).hexdigest()
        )

        return WebhookEvent(
            event_type=event_name,
            provider_reference=provider_ref,
            event_id=event_id,
            amount_kobo=amount_kobo,
            raw=payload,
        )


# ── Factory dependency for FastAPI ──────────────────────────────────

def get_payment_provider() -> PaymentProvider:
    """Build the configured provider. Swapped in tests via `app.dependency_overrides`."""
    settings = get_settings()
    if settings.payment_provider == "paystack":
        return PaystackProvider(secret_key=settings.paystack_secret_key)
    raise RuntimeError(
        f"Unknown payment provider: {settings.payment_provider!r}. "
        "Add a new branch here."
    )
