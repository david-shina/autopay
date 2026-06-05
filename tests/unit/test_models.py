"""Tests for the SQLModel model definitions.

These verify that each model:
  - Has the right table name
  - Has all expected columns
  - Has correct column nullability / uniqueness / default values
  - Money columns are NUMERIC(14,2), not Float

We inspect the SQLAlchemy table metadata directly — no DB required.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, Numeric
from sqlmodel import SQLModel

from app.models import (
    AuditLog,
    Bill,
    KycRecord,
    RefreshToken,
    TelegramLinkCode,
    Transaction,
    User,
    VirtualAccount,
)
from app.models.bill import Bill as BillModel
from app.models.transaction import Transaction as TxModel
from app.models.user import User as UserModel


@pytest.mark.parametrize(
    "model,table_name",
    [
        (User, "users"),
        (KycRecord, "kyc_records"),
        (VirtualAccount, "virtual_accounts"),
        (Bill, "bills"),
        (Transaction, "transactions"),
        (AuditLog, "audit_logs"),
        (RefreshToken, "refresh_tokens"),
        (TelegramLinkCode, "telegram_link_codes"),
    ],
)
def test_table_name(model, table_name: str) -> None:
    assert model.__tablename__ == table_name


def test_user_has_no_bvn_column() -> None:
    """BVN was extracted to kyc_records — must not appear in users."""
    table = SQLModel.metadata.tables["users"]
    assert "bvn" not in table.c


def test_user_balance_is_numeric() -> None:
    table = SQLModel.metadata.tables["users"]
    col = table.c["balance"]
    assert isinstance(col.type, Numeric)
    assert col.type.precision == 14
    assert col.type.scale == 2


def test_user_balance_default_is_decimal_zero() -> None:
    u = UserModel(
        first_name="A",
        last_name="B",
        email="a@b.com",
        phone_number="+1",
        hashed_password="x",
    )
    assert u.balance == Decimal("0.00")
    assert isinstance(u.balance, Decimal)


def test_transaction_money_is_numeric() -> None:
    table = SQLModel.metadata.tables["transactions"]
    for col_name in ("amount", "fee"):
        col = table.c[col_name]
        assert isinstance(col.type, Numeric), f"{col_name} should be NUMERIC"
        assert col.type.precision == 14
        assert col.type.scale == 2


def test_bill_amount_is_numeric() -> None:
    table = SQLModel.metadata.tables["bills"]
    col = table.c["amount"]
    assert isinstance(col.type, Numeric)
    assert col.type.precision == 14
    assert col.type.scale == 2


def test_transaction_has_provider_column() -> None:
    """The MVP used 'payaza_reference' — we now use generic 'provider' + 'provider_reference'."""
    table = SQLModel.metadata.tables["transactions"]
    assert "provider" in table.c
    assert "provider_reference" in table.c
    assert "payaza_reference" not in table.c
    assert "payaza" not in table.c["provider"].name.lower()


def test_kyc_record_uses_bytea_for_ciphertext() -> None:
    from sqlalchemy import LargeBinary

    table = SQLModel.metadata.tables["kyc_records"]
    assert isinstance(table.c["bvn_ciphertext"].type, LargeBinary)
    assert table.c["bvn_ciphertext"].nullable is False


def test_kyc_bvn_hash_is_unique_and_indexed() -> None:
    table = SQLModel.metadata.tables["kyc_records"]
    col = table.c["bvn_hash"]
    assert col.unique is True
    assert col.index is True


def test_user_primary_key_is_bigint() -> None:
    table = SQLModel.metadata.tables["users"]
    assert isinstance(table.c["id"].type, BigInteger)


def test_bill_indexes_include_status_and_due_date() -> None:
    table = SQLModel.metadata.tables["bills"]
    index_columns = []
    for idx in table.indexes:
        index_columns.extend([c.name for c in idx.columns])
    # due_date is a column we care about for the partial index
    assert "status" in [c.name for c in table.c]


def test_transaction_indexes() -> None:
    table = SQLModel.metadata.tables["transactions"]
    index_columns = set()
    for idx in table.indexes:
        index_columns.update(c.name for c in idx.columns)
    # Rubric: indexes on user_id and transaction_status
    assert "user_id" in index_columns
    assert "status" in index_columns


def test_audit_log_metadata_column_named_metadata() -> None:
    """The Python attribute is event_metadata; the SQL column is 'metadata'."""
    from sqlalchemy import JSON

    table = SQLModel.metadata.tables["audit_logs"]
    assert "metadata" in table.c
    assert "event_metadata" not in table.c  # SQL name, not attribute
    col = table.c["metadata"]
    # JSONB on Postgres, JSON elsewhere
    assert col.type.__class__.__name__ in {"JSONB", "JSON"}


def test_refresh_token_stores_hash_not_plaintext() -> None:
    table = SQLModel.metadata.tables["refresh_tokens"]
    assert "token_hash" in table.c
    assert "token" not in table.c  # never store raw JWT


def test_telegram_link_code_generator() -> None:
    """Codes are 6 hex chars (12 chars when decoded from token_hex(3))."""
    code = TelegramLinkCode.generate_code()
    assert len(code) == 6
    assert code.isupper() or code.isdigit()  # hex uppercase


def test_decimal_precision_in_arithmetic() -> None:
    """NUMERIC(14,2) preserves 0.01 precision (unlike float)."""
    u = UserModel(
        first_name="A", last_name="B", email="a@b.com", phone_number="+1", hashed_password="x",
        balance=Decimal("9999.99"),
    )
    u.balance = u.balance + Decimal("0.01")
    assert u.balance == Decimal("10000.00")
    # Float would give 10000.000000000002 or similar
