"""Tests for BVN encryption + hashing.

conftest.py sets a valid BVN_ENCRYPTION_KEY and clears the lru_cache
on _fernet() before any of these tests run.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.core import config as _config
from app.services import crypto as _crypto
from app.services.crypto import (
    BVNKeyError,
    decrypt_bvn,
    encrypt_bvn,
    hash_bvn,
    last4,
    mask,
)

VALID_BVN = "22123456789"


def test_encrypt_then_decrypt_roundtrip() -> None:
    ciphertext = encrypt_bvn(VALID_BVN)
    assert isinstance(ciphertext, bytes)
    assert len(ciphertext) > 0
    assert VALID_BVN.encode() not in ciphertext  # plaintext must not appear
    assert decrypt_bvn(ciphertext) == VALID_BVN


def test_encryption_is_nondeterministic() -> None:
    """Fernet uses a random IV; same plaintext → different ciphertexts."""
    a = encrypt_bvn(VALID_BVN)
    b = encrypt_bvn(VALID_BVN)
    assert a != b
    assert decrypt_bvn(a) == decrypt_bvn(b) == VALID_BVN


def test_tampered_ciphertext_raises() -> None:
    from cryptography.fernet import InvalidToken

    ciphertext = encrypt_bvn(VALID_BVN)
    tampered = bytearray(ciphertext)
    tampered[-10] ^= 0xFF
    with pytest.raises((ValueError, InvalidToken)):
        decrypt_bvn(bytes(tampered))


def test_hash_is_deterministic() -> None:
    """Same BVN → same hash, for dedupe lookups."""
    assert hash_bvn(VALID_BVN) == hash_bvn(VALID_BVN)


def test_hash_is_64_hex_chars() -> None:
    h = hash_bvn(VALID_BVN)
    assert len(h) == 64
    int(h, 16)  # raises if not hex


def test_different_bvns_produce_different_hashes() -> None:
    assert hash_bvn("22123456789") != hash_bvn("22987654321")


def test_invalid_bvn_format_rejected() -> None:
    for bad in ("", "123", "1234567890", "abcdefghijk", "2212345678901"):
        with pytest.raises(ValueError):
            encrypt_bvn(bad)
        with pytest.raises(ValueError):
            hash_bvn(bad)


def test_last4_and_mask() -> None:
    assert last4(VALID_BVN) == "6789"
    assert mask(VALID_BVN) == "******6789"


def test_missing_key_raises() -> None:
    """If BVN_ENCRYPTION_KEY is unset, encryption fails clearly."""
    with patch("app.services.crypto.settings") as mock_settings:
        mock_settings.bvn_encryption_key = ""
        _crypto._fernet.cache_clear()
        with pytest.raises(BVNKeyError):
            encrypt_bvn(VALID_BVN)


def test_invalid_fernet_key_raises() -> None:
    with patch("app.services.crypto.settings") as mock_settings:
        mock_settings.bvn_encryption_key = "not-a-real-fernet-key"
        _crypto._fernet.cache_clear()
        with pytest.raises(BVNKeyError):
            encrypt_bvn(VALID_BVN)
