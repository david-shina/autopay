"""Nomba implementation of the PaymentProvider protocol.

Reference: https://developer.nomba.com/

Two structural differences from a static-secret-key gateway drove the
shape of this file:

1. Auth is a 30-minute-lived OAuth-style access token (not a
   permanent secret key), so this class owns a small token manager
   (`_ensure_token` / `_issue_token` / `_refresh_token_call`) instead
   of just holding a static Authorization header.
2. Nomba has no "customer" or "transfer recipient" resource the way
   Paystack does -- account holder details are passed directly to
   `create_virtual_account` / `initiate_transfer`. `create_customer`
   and `create_transfer_recipient` are therefore no-ops here; they
   exist only to satisfy the shared `PaymentProvider` protocol so
   `payout.py` / `wallet.py` don't need per-provider branches.

All amounts are kept in AutoPay's internal integer-kobo unit at the
protocol boundary; this class converts to/from Nomba's decimal-Naira
wire format internally.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

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
    InsufficientFunds,
    InvalidAccount,
    KYCRequired,
    PaymentError,
    ProviderError,
    WebhookSignatureError,
)

logger = logging.getLogger(__name__)

NOMBA_BASE = "https://api.nomba.com"
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)
ACCESS_TOKEN_DEFAULT_TTL = timedelta(minutes=30)


# ── Nomba-specific error mapping ────────────────────────────────────
#
# NOTE: only the happy-path response shapes are confirmed from Nomba's
# docs. Their error-message catalog is not, so these are keyword
# heuristics (same spirit as Paystack's old `_map_paystack_error`) --
# test against real sandbox error responses and tighten before relying
# on this in production.

def _map_nomba_error(status_code: int, body: dict) -> PaymentError:
    message = body.get("description") or body.get("message") or "Unknown Nomba error"
    raw = body.get("data") or {}
    lower = message.lower()

    if status_code in (401, 403):
        return AuthenticationError(message, provider="nomba", raw=raw)

    if "insufficient" in lower or "balance" in lower:
        return InsufficientFunds(message, provider="nomba", raw=raw)

    if "bvn" in lower or "kyc" in lower:
        return KYCRequired(message, provider="nomba", raw=raw)

    if "name" in lower and "mismatch" in lower:
        return AccountNameMismatch(message, provider="nomba", raw=raw)

    if "account" in lower and ("invalid" in lower or "not found" in lower or "resolve" in lower):
        return InvalidAccount(message, provider="nomba", raw=raw)

    return ProviderError(message, provider="nomba", raw=raw)


# ── Provider implementation ─────────────────────────────────────────

class NombaProvider(PaymentProvider):
    name = "nomba"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        account_id: str,
        webhook_secret: str,
        base_url: str = NOMBA_BASE,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._account_id = account_id
        self._webhook_secret = webhook_secret
        self._base_url = base_url
        self._timeout = timeout

        self._client: Optional[httpx.AsyncClient] = None

        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._token_lock = asyncio.Lock()

    async def _client_lazy(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Token lifecycle ──────────────────────────────────────────────

    @staticmethod
    def _parse_expiry(raw: Any) -> datetime:
        """Best-effort parse of Nomba's `expiresAt`. The exact wire
        format isn't confirmed from docs, so unparseable values fall
        back to the documented 30-minute token lifetime."""
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
        elif isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                pass
        logger.warning("Could not parse Nomba expiresAt=%r; assuming 30 min TTL", raw)
        return datetime.now(timezone.utc) + ACCESS_TOKEN_DEFAULT_TTL

    def _token_is_fresh(self) -> bool:
        return (
            self._access_token is not None
            and self._token_expires_at is not None
            and datetime.now(timezone.utc) < self._token_expires_at - TOKEN_REFRESH_BUFFER
        )

    async def _issue_token(self) -> None:
        client = await self._client_lazy()
        try:
            resp = await client.post(
                "/v1/auth/token/issue",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/json", "accountId": self._account_id},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Nomba transport error during token issue: {exc}", provider="nomba"
            ) from exc

        body = resp.json()
        if resp.status_code >= 400 or body.get("code") not in (None, "00"):
            raise AuthenticationError(
                body.get("description") or body.get("message") or "Nomba token issue failed",
                provider="nomba",
                raw=body,
            )

        self._access_token = body["access_token"]
        self._refresh_token = body.get("refresh_token")
        self._token_expires_at = self._parse_expiry(body.get("expiresAt"))

    async def _refresh_token_call(self) -> None:
        if not self._refresh_token:
            await self._issue_token()
            return

        client = await self._client_lazy()
        try:
            resp = await client.post(
                "/v1/auth/token/refresh",
                json={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
                headers={
                    "Content-Type": "application/json",
                    "accountId": self._account_id,
                    "Authorization": f"Bearer {self._access_token}",
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Nomba transport error during token refresh: {exc}", provider="nomba"
            ) from exc

        body = resp.json()
        if resp.status_code >= 400 or body.get("code") not in (None, "00"):
            # Refresh token may itself be expired/revoked -- fall back to a
            # full re-issue rather than surfacing this as a hard failure.
            await self._issue_token()
            return

        self._access_token = body["access_token"]
        self._refresh_token = body.get("refresh_token", self._refresh_token)
        self._token_expires_at = self._parse_expiry(body.get("expiresAt"))

    async def _ensure_token(self) -> str:
        if self._token_is_fresh():
            return self._access_token  # type: ignore[return-value]
        async with self._token_lock:
            if self._token_is_fresh():
                return self._access_token  # type: ignore[return-value]
            if self._access_token is None:
                await self._issue_token()
            else:
                await self._refresh_token_call()
        return self._access_token  # type: ignore[return-value]

    # ── HTTP plumbing ──────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        _retried: bool = False,
    ) -> dict:
        """Make a Nomba call and return the `data` payload.

        Raises a typed `PaymentError` subclass on any non-2xx response.
        A single 401/403 triggers one forced token refresh + retry
        (handles the token expiring between `_ensure_token`'s check and
        this call landing), then gives up.
        """
        token = await self._ensure_token()
        client = await self._client_lazy()
        headers = {
            "Content-Type": "application/json",
            "accountId": self._account_id,
            "Authorization": f"Bearer {token}",
        }
        try:
            resp = await client.request(method, path, json=json_body, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Nomba transport error: {exc}", provider="nomba") from exc

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"Nomba returned non-JSON ({resp.status_code})", provider="nomba"
            ) from exc

        if resp.status_code in (401, 403) and not _retried:
            async with self._token_lock:
                self._access_token = None
            return await self._request(method, path, json_body=json_body, params=params, _retried=True)

        if resp.status_code >= 400:
            raise _map_nomba_error(resp.status_code, body)

        return body.get("data") or {}

    # ── Customer + virtual account ───────────────────────────────────

    async def create_customer(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone: Optional[str] = None,
    ) -> str:
        """Nomba has no separate customer resource -- account holder
        details go directly to `create_virtual_account`. This just
        mints a stable local `accountRef` (no API call)."""
        return f"apy-{uuid.uuid4().hex[:24]}"

    async def create_virtual_account(
        self,
        *,
        customer_code: str,
        preferred_bank: Optional[str] = None,
        account_name: Optional[str] = None,
    ) -> VirtualAccountData:
        if not account_name:
            raise ProviderError(
                "Nomba requires account_name to create a virtual account", provider="nomba"
            )

        # No expiryDate/expectedAmount => static (permanent, reusable)
        # account -- matches the always-on wallet-topup NUBAN model the
        # rest of the app assumes.
        data = await self._request(
            "POST",
            "/v1/accounts/virtual",
            json_body={"accountRef": customer_code, "accountName": account_name},
        )

        return VirtualAccountData(
            account_number=str(data["bankAccountNumber"]),
            account_name=str(data["bankAccountName"]),
            bank_name=str(data["bankName"]),
            bank_code="",  # Nomba's response doesn't return one; unused downstream today.
            provider_reference=str(data["accountHolderId"]),
            provider=self.name,
        )

    # ── Account resolution + transfers ────────────────────────────────

    async def resolve_account(
        self,
        *,
        account_number: str,
        bank_code: str,
    ) -> ResolvedAccount:
        data = await self._request(
            "POST",
            "/v1/transfers/bank/lookup",
            json_body={"accountNumber": account_number, "bankCode": bank_code},
        )
        return ResolvedAccount(
            account_number=str(data["accountNumber"]),
            account_name=str(data["accountName"]),
            bank_code=bank_code,  # echoed back; Nomba's response doesn't include it
        )

    async def create_transfer_recipient(
        self,
        *,
        account_number: str,
        bank_code: str,
        account_name: str,
    ) -> str:
        """Nomba has no server-side recipient object -- `initiate_transfer`
        takes the account details directly. No-op; returns
        `account_number` so callers always get a non-empty string."""
        return account_number

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
        if not account_number or not bank_code or not account_name:
            raise ProviderError(
                "Nomba transfers require account_number, bank_code and account_name",
                provider="nomba",
            )

        amount_naira = Decimal(amount_kobo) / Decimal(100)
        data = await self._request(
            "POST",
            "/v2/transfers/bank",
            json_body={
                "amount": float(amount_naira),
                "accountNumber": account_number,
                "accountName": account_name,
                "bankCode": bank_code,
                "merchantTxRef": reference,
                "senderName": "AutoPay",
                "narration": reason,
            },
        )

        status_map = {
            "SUCCESS": "success",
            "FAILED": "failed",
            "PENDING_BILLING": "pending",
            "PROCESSING": "pending",
        }
        return TransferResult(
            provider_reference=reference,
            provider_transfer_id=str(data.get("id") or ""),
            status=status_map.get(str(data.get("status") or "").upper(), "pending"),
            raw_response=data,
        )

    # ── Webhook signature + parsing ───────────────────────────────────

    def verify_webhook_signature(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> bool:
        signature = headers.get("nomba-signature") or ""
        if not signature:
            return False

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False

        # Docs describe the signed string as a colon-joined sequence of
        # event_type, requestId, and several transaction fields, plus
        # the timestamp header -- but don't confirm whether those
        # transaction fields sit under `data` directly or `data.transaction`.
        # Handle both shapes defensively; confirm against a real sandbox
        # delivery before trusting this in production.
        data = payload.get("data") or {}
        transaction = data.get("transaction") or data

        signed_string = ":".join(
            str(part) for part in (
                payload.get("event_type", ""),
                payload.get("requestId", ""),
                data.get("userId", ""),
                data.get("walletId", ""),
                transaction.get("transactionId", ""),
                transaction.get("type", ""),
                transaction.get("time", ""),
                transaction.get("responseCode", ""),
                headers.get("nomba-timestamp", ""),
            )
        )
        digest = hmac.new(
            self._webhook_secret.encode("utf-8"), signed_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    async def parse_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> WebhookEvent:
        if not self.verify_webhook_signature(raw_body=raw_body, headers=headers):
            raise WebhookSignatureError("Invalid Nomba webhook signature", provider="nomba")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookSignatureError(
                f"Webhook body is not valid JSON: {exc}", provider="nomba"
            ) from exc

        event_type_raw = str(payload.get("event_type") or "")
        data = payload.get("data") or {}
        transaction = data.get("transaction") or data

        event_map = {
            "payment_success": "charge.success",
            "payout_success": "transfer.success",
            "payout_failed": "transfer.failed",
            "payout_refund": "transfer.reversed",
        }
        event_type = event_map.get(event_type_raw)
        if event_type is None:
            logger.warning("Unknown/unmapped Nomba webhook event: %s", event_type_raw)
            event_type = event_type_raw

        # NOT CONFIRMED from docs: whether the payout webhook echoes back
        # our `merchantTxRef`. `confirm_payout()` matches on our own
        # reference, not Nomba's `transactionId` -- verify this against a
        # real sandbox payout webhook before going live, or transfers will
        # never leave "processing".
        provider_ref = str(
            transaction.get("merchantTxRef") or transaction.get("transactionId") or ""
        )

        amount_kobo: Optional[int] = None
        raw_amount = transaction.get("transactionAmount")
        if raw_amount is not None:
            amount_kobo = int(Decimal(str(raw_amount)) * 100)

        event_id = str(payload.get("requestId") or hashlib.sha256(raw_body).hexdigest())

        return WebhookEvent(
            event_type=event_type,
            provider_reference=provider_ref,
            event_id=event_id,
            amount_kobo=amount_kobo,
            raw=payload,
        )


# ── Factory dependency for FastAPI ──────────────────────────────────

def get_payment_provider() -> PaymentProvider:
    """Build the configured provider. Swapped in tests via `app.dependency_overrides`."""
    settings = get_settings()
    if settings.payment_provider == "nomba":
        return NombaProvider(
            client_id=settings.nomba_client_id,
            client_secret=settings.nomba_client_secret,
            account_id=settings.nomba_account_id,
            webhook_secret=settings.nomba_webhook_secret,
            base_url=settings.nomba_base_url,
        )
    raise RuntimeError(
        f"Unknown payment provider: {settings.payment_provider!r}. "
        "Add a new branch here."
    )
