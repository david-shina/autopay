"""Dev seed data — idempotent.

Inserts 2 demo users (with KYC, virtual accounts), 1 demo bill, 1
historical credit transaction, and a few audit log entries.

Run with:
  make seed
or directly:
  python -m scripts.seed
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

# Make `app` importable when running this file as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select

from app.core.database import engine
from app.core.security import hash_password
from app.models.bill import Bill
from app.models.kyc import KycRecord
from app.models.transaction import Transaction
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.services.audit import audit_user_signup, audit_wallet_credit
from app.services.crypto import encrypt_bvn, hash_bvn, last4

DEMO_USERS = [
    {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "phone_number": "+2348012345678",
        "password": "demo-password-1",
        "bvn": "22123456789",
        "balance": Decimal("150000.00"),
        "account_number": "8123456789",
        "bank_name": "Wema Bank",
        "provider_reference": "va_ref_ada_001",
    },
    {
        "first_name": "Tunde",
        "last_name": "Bello",
        "email": "tunde@example.com",
        "phone_number": "+2348087654321",
        "password": "demo-password-2",
        "bvn": "22987654321",
        "balance": Decimal("42500.00"),
        "account_number": "8987654321",
        "bank_name": "Moniepoint MFB",
        "provider_reference": "va_ref_tunde_001",
    },
]


def seed() -> None:
    with Session(engine) as session:
        if session.exec(select(User)).first():
            print("[seed] demo data already present, skipping")
            return

        for u in DEMO_USERS:
            user = User(
                first_name=u["first_name"],
                last_name=u["last_name"],
                email=u["email"],
                phone_number=u["phone_number"],
                hashed_password=hash_password(u["password"]),
                balance=u["balance"],
                address="1 Marina Road, Lagos",
            )
            session.add(user)
            session.flush()

            kyc = KycRecord(
                user_id=user.id,
                bvn_ciphertext=encrypt_bvn(u["bvn"]),
                bvn_last4=last4(u["bvn"]),
                bvn_hash=hash_bvn(u["bvn"]),
                bvn_validated=True,
                validated_at=user.created_at,
            )
            session.add(kyc)

            va = VirtualAccount(
                user_id=user.id,
                provider="paystack",
                provider_account_reference=u["provider_reference"],
                account_number=u["account_number"],
                account_name=f"{u['first_name']} {u['last_name']}",
                bank_name=u["bank_name"],
                currency="NGN",
            )
            session.add(va)

            audit_user_signup(session, user_id=user.id, ip="127.0.0.1")

            # Top-up transaction so the audit + balance match
            topup = Transaction(
                user_id=user.id,
                type="credit",
                amount=u["balance"],
                fee=Decimal("0.00"),
                currency="NGN",
                status="success",
                provider="paystack",
                provider_reference=f"seed_topup_{user.id}",
                narration="Initial demo top-up",
            )
            session.add(topup)
            session.flush()
            audit_wallet_credit(
                session,
                user_id=user.id,
                amount=float(u["balance"]),
                provider_reference=f"seed_topup_{user.id}",
                new_balance=float(u["balance"]),
                actor="system",
            )

        # One demo bill for Ada
        ada = session.exec(select(User).where(User.email == "ada@example.com")).one()
        bill = Bill(
            user_id=ada.id,
            vendor_name="Lekki Phase 1 Estate",
            amount=Decimal("250000.00"),
            currency="NGN",
            due_date=user_due_date(7),
            account_number="0123456789",
            bank_code="058",
            bank_name="GTBank",
            status="pending",
        )
        session.add(bill)

        session.commit()
        print(f"[seed] inserted {len(DEMO_USERS)} users + 1 bill")


def user_due_date(days_from_now: int):
    from datetime import datetime, timedelta, timezone

    return datetime.now(tz=timezone.utc) + timedelta(days=days_from_now)


if __name__ == "__main__":
    seed()
