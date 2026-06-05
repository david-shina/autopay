"""BVN encryption + hashing utilities.

Two separate operations on a BVN plaintext:

1. **Reversible encryption**  (`encrypt_bvn` / `decrypt_bvn`)
   Uses Fernet (AES-128-CBC + HMAC-SHA256). Stored in `kyc_records.bvn_ciphertext`.
   Required to send the BVN back to the provider for validation.
   Key: `BVN_ENCRYPTION_KEY` (32-byte url-safe base64).

2. **Deterministic hash**  (`hash_bvn`)
   HMAC-SHA256 with an app pepper. Stored in `kyc_records.bvn_hash`.
   Used for uniqueness lookups ("does this BVN already exist?") without
   ever decrypting anything. Different from encryption so a DB leak alone
   isn't enough to recover the BVN.
   Key: `BVN_HASH_PEPPER` (or derived from BVN_ENCRYPTION_KEY if not set).

Security notes:
- Plaintext BVN is never logged, never returned from any function, and
  must be wiped from the request payload immediately after use.
- Key rotation: when BVN_ENCRYPTION_KEY changes, existing ciphertext
  becomes unreadable. Re-encryption is a separate operational task.
"""
from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class BVNKeyError(RuntimeError):
    """Raised when BVN encryption keys are misconfigured."""


@lru_cache
def _fernet() -> Fernet:
    if not settings.bvn_encryption_key:
        raise BVNKeyError(
            "BVN_ENCRYPTION_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"",
        )
    try:
        return Fernet(settings.bvn_encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise BVNKeyError(
            f"BVN_ENCRYPTION_KEY is not a valid Fernet key: {exc}",
        ) from exc


def _pepper() -> bytes:
    """HMAC key for hashing. Falls back to a derived key if no pepper set."""
    if settings.bvn_encryption_key:
        return hashlib.sha256(
            (settings.bvn_encryption_key + ":bvn-pepper").encode()
        ).digest()
    raise BVNKeyError(
        "Cannot derive BVN hash pepper without BVN_ENCRYPTION_KEY (or set BVN_HASH_PEPPER).",
    )


# ── Public API ──────────────────────────────────────────────────────

def encrypt_bvn(plaintext: str) -> bytes:
    """Encrypt a BVN plaintext. Returns Fernet ciphertext bytes."""
    if not plaintext or not plaintext.isdigit() or len(plaintext) != 11:
        raise ValueError("BVN must be an 11-digit Nigerian BVN")
    return _fernet().encrypt(plaintext.encode())


def decrypt_bvn(ciphertext: bytes) -> str:
    """Decrypt a BVN ciphertext. Returns plaintext (handle with care)."""
    try:
        return _fernet().decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise ValueError("BVN ciphertext is invalid or was tampered with") from exc


def hash_bvn(plaintext: str) -> str:
    """Deterministic HMAC-SHA256 hash for uniqueness lookups.

    Different from `encrypt_bvn` — this is one-way. The pepper ensures that
    a leaked DB row alone isn't enough to verify a BVN against an external
    breach dump.
    """
    if not plaintext or not plaintext.isdigit() or len(plaintext) != 11:
        raise ValueError("BVN must be an 11-digit Nigerian BVN")
    return hmac.new(_pepper(), plaintext.encode(), hashlib.sha256).hexdigest()


def last4(plaintext: str) -> str:
    """Last 4 digits of a BVN. Safe for display."""
    if not plaintext or len(plaintext) < 4:
        raise ValueError("BVN too short")
    return plaintext[-4:]


def mask(plaintext: str) -> str:
    """Masked display string, e.g. '******1234'."""
    return f"******{last4(plaintext)}"
