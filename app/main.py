"""FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import __version__
# Importing models registers them on SQLModel.metadata (needed by Alembic +
# init_db). The side-effect import is intentional; do not remove.
from app import models  # noqa: F401
from app.api import auth_router, bills_router, kyc_router, wallet_router, webhooks_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.scheduler import start_scheduler, stop_scheduler
from app.services.telegram import (
    start_bot,
    stop_bot,
    webhook_router as telegram_webhook_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # Background workers (single process by design — see Dockerfile).
    # Order matters: scheduler first so a job that fires on startup
    # doesn't race with bot startup; bot second so a Telegram update
    # that triggers DB work sees a ready connection.
    start_scheduler()
    await start_bot()
    try:
        yield
    finally:
        await stop_bot()
        stop_scheduler()


app = FastAPI(
    title="AutoPay AI",
    version=__version__,
    description="AI-powered bill automation for Nigerian users.",
    lifespan=lifespan,
)

# Health (unversioned — used by Docker, k8s, load balancers)
app.include_router(health_router)

# Versioned API
app.include_router(auth_router,    prefix="/api/v1/auth")
app.include_router(bills_router,   prefix="/api/v1/bills")
app.include_router(kyc_router,     prefix="/api/v1/kyc")
app.include_router(wallet_router,  prefix="/api/v1/wallet")

# Webhooks (unversioned — provider-side; Paystack + Telegram hardcode the URL)
app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(telegram_webhook_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):  # noqa: ARG001
    """Catch-all so we never leak stack traces in production."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "env": settings.environment},
    )
