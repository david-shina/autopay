"""Typed errors raised by payment provider implementations.

Business code (payout, webhook handler) catches these and translates
them into user-facing HTTP responses + audit rows. The provider layer
must NEVER leak raw `httpx.HTTPError` / JSON to the caller — wrap
everything in one of these.
"""
from __future__ import annotations

from typing import Optional


class PaymentError(Exception):
    """Base class for all payment-gateway failures."""

    def __init__(self, message: str, *, provider: str, raw: Optional[dict] = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.raw = raw or {}


class ProviderError(PaymentError):
    """Generic 4xx/5xx/network failure from the gateway."""


class AuthenticationError(PaymentError):
    """Bad/expired API key, missing required header, etc."""


class InvalidAccount(PaymentError):
    """The account number/bank_code pair didn't resolve cleanly."""


class AccountNameMismatch(PaymentError):
    """The account resolved, but the name didn't match the user on file."""


class InsufficientFunds(PaymentError):
    """The provider refused the transfer because our balance is too low."""


class KYCRequired(PaymentError):
    """The provider requires more KYC info than we have on file."""


class WebhookSignatureError(PaymentError):
    """Webhook signature verification failed — do not retry, do not parse."""
