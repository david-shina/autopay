"""Tests for the Paystack provider.

We use `respx` to mock httpx calls so no real network is made.
The `verify_webhook_signature` test exercises real HMAC math (no mock).
"""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app.services.payments import (
    AccountNameMismatch,
    AuthenticationError,
    InvalidAccount,
    KYCRequired,
    PaystackProvider,
    ProviderError,
    WebhookSignatureError,
)

PAYSTACK = "https://api.paystack.co"
SECRET = "sk_test_d4e5f6a1b2c3d4e5f6a7b8c9d0e1f2a3"


def _client() -> PaystackProvider:
    p = PaystackProvider(secret_key=SECRET)
    return p


# ── create_customer ────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_create_customer_happy_path() -> None:
    respx.post(f"{PAYSTACK}/customer").mock(
        return_value=httpx.Response(
            200, json={"status": True, "message": "Customer created", "data": {"customer_code": "CUS_x"}}
        )
    )
    p = _client()
    code = await p.create_customer(
        email="ada@example.com", first_name="Ada", last_name="Lovelace"
    )
    assert code == "CUS_x"


@pytest.mark.asyncio
@respx.mock
async def test_create_customer_auth_error() -> None:
    respx.post(f"{PAYSTACK}/customer").mock(
        return_value=httpx.Response(401, json={"status": False, "message": "Invalid key", "data": None})
    )
    p = _client()
    with pytest.raises(AuthenticationError):
        await p.create_customer(
            email="ada@example.com", first_name="Ada", last_name="Lovelace"
        )


# ── create_virtual_account ─────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_create_virtual_account_parses_response() -> None:
    respx.post(f"{PAYSTACK}/dedicated_account").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "message": "Dedicated account created",
                "data": {
                    "id": 42,
                    "account_number": "0123456789",
                    "account_name": "Ada Lovelace",
                    "bank": {"id": "058", "name": "GTBank"},
                },
            },
        )
    )
    p = _client()
    va = await p.create_virtual_account(customer_code="CUS_x")
    assert va.account_number == "0123456789"
    assert va.bank_name == "GTBank"
    assert va.bank_code == "058"
    assert va.provider_reference == "42"
    assert va.provider == "paystack"


# ── resolve_account ────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_resolve_account() -> None:
    respx.get(f"{PAYSTACK}/bank/resolve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "account_number": "0123456789",
                    "account_name": "DSTV NG LTD",
                    "bank_code": "058",
                },
            },
        )
    )
    p = _client()
    out = await p.resolve_account(account_number="0123456789", bank_code="058")
    assert out.account_name == "DSTV NG LTD"
    assert out.bank_code == "058"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_account_invalid() -> None:
    respx.get(f"{PAYSTACK}/bank/resolve").mock(
        return_value=httpx.Response(
            400, json={"status": False, "message": "Invalid account number", "data": None}
        )
    )
    p = _client()
    with pytest.raises(InvalidAccount):
        await p.resolve_account(account_number="0000000000", bank_code="058")


# ── initiate_transfer ──────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_initiate_transfer() -> None:
    respx.post(f"{PAYSTACK}/transfer").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "id": 99,
                    "reference": "autopay_1_abc",
                    "status": "pending",
                    "amount": 500000,
                },
            },
        )
    )
    p = _client()
    out = await p.initiate_transfer(
        amount_kobo=500000,
        recipient_code="RCP_x",
        reference="autopay_1_abc",
        reason="AutoPay: DSTV",
    )
    assert out.provider_reference == "autopay_1_abc"
    assert out.provider_transfer_id == "99"
    assert out.status == "pending"


# ── Webhook signature: real HMAC math ──────────────────────────────

def test_verify_webhook_signature_valid() -> None:
    p = _client()
    body = b'{"event":"charge.success","data":{"reference":"r1"}}'
    expected = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    assert p.verify_webhook_signature(raw_body=body, signature_header=expected) is True


def test_verify_webhook_signature_invalid() -> None:
    p = _client()
    body = b'{"event":"charge.success"}'
    assert p.verify_webhook_signature(raw_body=body, signature_header="deadbeef") is False


def test_verify_webhook_signature_missing_header() -> None:
    p = _client()
    assert p.verify_webhook_signature(raw_body=b"x", signature_header="") is False


def test_verify_webhook_signature_tampered_body() -> None:
    p = _client()
    body = b'{"event":"charge.success","data":{"amount":1000}}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    tampered = body.replace(b"1000", b"99999")
    assert p.verify_webhook_signature(raw_body=tampered, signature_header=sig) is False


# ── parse_webhook ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_webhook_charge_success() -> None:
    p = _client()
    payload = {
        "event": "charge.success",
        "data": {"reference": "r1", "amount": 15000},
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    event = await p.parse_webhook(raw_body=raw, signature_header=sig)
    assert event.event_type == "charge.success"
    assert event.provider_reference == "r1"
    assert event.amount_kobo == 15000


@pytest.mark.asyncio
async def test_parse_webhook_transfer_success() -> None:
    p = _client()
    payload = {
        "event": "transfer.success",
        "data": {"id": 5, "reference": "autopay_1_x", "amount": 100000},
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    event = await p.parse_webhook(raw_body=raw, signature_header=sig)
    assert event.event_type == "transfer.success"
    assert event.provider_reference == "autopay_1_x"
    assert event.amount_kobo == 100000


@pytest.mark.asyncio
async def test_parse_webhook_rejects_bad_signature() -> None:
    p = _client()
    raw = b'{"event":"charge.success","data":{"reference":"r1"}}'
    with pytest.raises(WebhookSignatureError):
        await p.parse_webhook(raw_body=raw, signature_header="bad-sig")


@pytest.mark.asyncio
async def test_parse_webhook_rejects_non_json() -> None:
    p = _client()
    raw = b"not json at all"
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    with pytest.raises(WebhookSignatureError):
        await p.parse_webhook(raw_body=raw, signature_header=sig)


@pytest.mark.asyncio
async def test_parse_webhook_unknown_event_passes_through() -> None:
    p = _client()
    payload = {"event": "satellite.launched", "data": {"id": 1}}
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    event = await p.parse_webhook(raw_body=raw, signature_header=sig)
    assert event.event_type == "satellite.launched"
    assert event.provider_reference == "1"


# ── transport error path ────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_network_error_wrapped_as_provider_error() -> None:
    respx.post(f"{PAYSTACK}/customer").mock(side_effect=httpx.ConnectError("nope"))
    p = _client()
    with pytest.raises(ProviderError):
        await p.create_customer(
            email="x@x.com", first_name="x", last_name="y"
        )


# ── error mapping extras ───────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_kyc_required_error_mapping() -> None:
    respx.post(f"{PAYSTACK}/transfer").mock(
        return_value=httpx.Response(
            400, json={"status": False, "message": "BVN is required for this transfer", "data": None}
        )
    )
    p = _client()
    with pytest.raises(KYCRequired):
        await p.initiate_transfer(
            amount_kobo=100, recipient_code="R", reference="r", reason="x"
        )
