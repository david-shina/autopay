"""Tests for password hashing + JWT helpers.

conftest.py sets a valid JWT_SECRET_KEY before any of these tests run.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from app.core import config as _config
from app.core.security import (
    JWTError_,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    plain = "S3cur3P@ssw0rd!"
    hashed = hash_password(plain)
    assert hashed != plain
    assert hashed.startswith("$2")  # bcrypt prefix
    assert verify_password(plain, hashed) is True


def test_wrong_password_rejected() -> None:
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("wrong", hashed) is False


def test_empty_inputs_rejected() -> None:
    assert verify_password("", "anything") is False
    assert verify_password("anything", "") is False
    with pytest.raises(ValueError):
        hash_password("")


def test_jwt_roundtrip() -> None:
    token = create_access_token("42")
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"


def test_jwt_tampered_rejected() -> None:
    token = create_access_token("1")
    tampered = token[:-2] + ("XX" if token[-2:] != "XX" else "YY")
    with pytest.raises(JWTError_):
        decode_token(tampered)


def test_refresh_token_has_correct_type() -> None:
    token, _ = create_refresh_token("7")
    payload = decode_token(token, expected_type="refresh")
    assert payload["type"] == "refresh"


def test_access_token_rejected_as_refresh() -> None:
    token = create_access_token("7")
    with pytest.raises(JWTError_):
        decode_token(token, expected_type="refresh")


def test_missing_secret_raises() -> None:
    with patch("app.core.security.settings") as m:
        m.jwt_secret_key = ""
        with pytest.raises(JWTError_):
            create_access_token("1")


def test_custom_ttl() -> None:
    token = create_access_token("1", expires_delta=timedelta(seconds=1))
    payload = decode_token(token)
    assert payload["exp"] - payload["iat"] == 1
