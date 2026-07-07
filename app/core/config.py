from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    secret_key: str = "change-me-to-a-32-char-random-string"
    app_port: int = 8000

    # ── Database ─────────────────────────────────────────────────────
    postgres_user: str = "postgres"
    postgres_password: str = ""
    postgres_db: str = "autopay"
    postgres_port: int = 5432
    database_url: str = ""
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_echo: bool = False

    # ── Payment provider ─────────────────────────────────────────────
    payment_provider: Literal["nomba"] = "nomba"

    # ── Nomba ────────────────────────────────────────────────────────
    nomba_client_id: str = ""
    nomba_client_secret: str = ""
    nomba_account_id: str = ""
    nomba_webhook_secret: str = ""
    nomba_base_url: str = "https://sandbox.nomba.com"

    # ── Telegram ─────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    webhook_url: str = ""

    # ── LLM / LangChain ──────────────────────────────────────────────
    groq_api_key: str = ""
    google_api_key: str = ""
    langchain_api_key: str = ""
    langchain_tracing_v2: bool = False

    # ── Fees ─────────────────────────────────────────────────────────
    payout_fee_ngn: Decimal = Decimal("50.00")

    # ── Security ─────────────────────────────────────────────────────
    bvn_encryption_key: str = ""
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 7

    # ── Feature flags ────────────────────────────────────────────────
    auto_provision_dva_on_signup: bool = False

    # ── Derived ──────────────────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_enabled(self) -> bool:
        return bool(self.groq_api_key or self.google_api_key)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def nomba_sandbox(self) -> bool:
        return "sandbox" in self.nomba_base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
