"""Smoke tests — confirm the app skeleton imports and settings are valid.

These run with no external dependencies.
"""
from app import __version__
from app.core.config import get_settings, Settings


def test_settings_load() -> None:
    """Settings class can be constructed and exposes expected defaults."""
    s = get_settings()
    assert s.app_name == "auto-pay-ai"
    assert s.environment in {"development", "staging", "production", "test"}
    assert s.database_url.startswith("postgresql://")


def test_database_url_quoted_handling() -> None:
    """Pydantic should strip surrounding quotes from DATABASE_URL."""
    s = Settings(database_url='"postgresql://u:p@h:5432/d"')
    assert s.database_url == "postgresql://u:p@h:5432/d"


def test_app_version_is_set() -> None:
    """Version literal is set in app/__init__.py."""
    assert __version__ == "0.2.0"


def test_is_production_and_is_test_helpers() -> None:
    """Helpers reflect the current environment.

    Conftest sets ENVIRONMENT=test, so is_test is True.
    """
    s = get_settings()
    assert s.is_production is False
    assert s.is_test is True  # conftest sets ENVIRONMENT=test
