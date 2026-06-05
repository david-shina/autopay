"""Liveness and readiness probes.

- /healthz  liveness: process is up
- /readyz   readiness: dependencies (DB) are reachable
- /         banner: returns app version + env
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session

from app import __version__
from app.core.config import settings
from app.core.database import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
def healthz() -> dict:
    return {"status": "alive"}


@router.get("/readyz", summary="Readiness probe (checks DB)")
def readyz(session: Session = Depends(get_session)) -> JSONResponse:
    try:
        session.exec(text("SELECT 1")).one()
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "database": "ok"},
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "database": str(exc)},
        )


@router.get("/", summary="App banner")
def banner() -> dict:
    return {
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
    }
