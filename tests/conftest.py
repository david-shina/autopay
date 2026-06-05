"""Shared pytest fixtures + test env setup.

This conftest runs before any test module is imported. We:

  1. Set a known JWT secret + generate a valid Fernet key.
  2. Clear the cached Settings + Fernet singletons so they pick up
     the env values we just set.
  3. Provide shared fixtures.
"""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

# ── Set env vars BEFORE any `from app...` import below ─────────────
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-must-be-at-least-32-chars-long")
os.environ.setdefault("BVN_ENCRYPTION_KEY", Fernet.generate_key().decode())

# Now we can import app modules safely
from app.core import config as _config  # noqa: E402

# Clear any cached settings from a prior import
_config.get_settings.cache_clear()

import app.services.crypto as _crypto  # noqa: E402

_crypto._fernet.cache_clear()


@pytest.fixture
def sample_user_dict() -> dict:
    """Placeholder factory for tests that land in Chunk 2."""
    return {
        "email": "test@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
