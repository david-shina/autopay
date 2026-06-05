"""SQLAlchemy / SQLModel engine + session management."""
from collections.abc import Generator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.engine import Engine

from app.core.config import settings


def _build_engine() -> Engine:
    """Create the SQLAlchemy engine with sane pool defaults for Postgres."""
    return create_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        pool_recycle=300,
    )


engine: Engine = _build_engine()


def init_db() -> None:
    """Create all tables. Used in tests; production uses Alembic."""
    # Importing models registers them on SQLModel.metadata
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a session and closes it after the request."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context-manager variant for non-FastAPI callers (workers, scripts)."""
    with Session(engine) as session:
        yield session
