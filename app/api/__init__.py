"""FastAPI routers — all HTTP endpoints under one namespace."""
from app.api.auth import router as auth_router
from app.api.bills import router as bills_router
from app.api.kyc import router as kyc_router
from app.api.wallet import router as wallet_router
from app.api.webhooks import router as webhooks_router

__all__ = [
    "auth_router",
    "bills_router",
    "kyc_router",
    "wallet_router",
    "webhooks_router",
]
