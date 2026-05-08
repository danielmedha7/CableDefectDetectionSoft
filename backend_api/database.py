"""Async SQLAlchemy database wiring.

Single source of truth for the engine, session factory, and Base class.
Defaults to SQLite (`./data/cablevision.db`) but honours the `db_url`
from configs/config.yaml or the env var `CABLEVISION_DB_URL`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

log = logging.getLogger(__name__)


def _resolve_db_url() -> str:
    env = os.environ.get("CABLEVISION_DB_URL")
    if env:
        return env
    try:
        import yaml
        cfg_path = Path(__file__).resolve().parents[1] / "configs" / "config.yaml"
        if cfg_path.exists():
            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            url = (cfg.get("api", {}) or {}).get("db_url")
            if url:
                return url
    except Exception as exc:
        log.warning("Could not read db_url from config.yaml: %s", exc)
    Path("./data").mkdir(parents=True, exist_ok=True)
    return "sqlite+aiosqlite:///./data/cablevision.db"


def _to_async_url(url: str) -> str:
    """Normalise sync URLs to their async equivalents."""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


DATABASE_URL = _to_async_url(_resolve_db_url())
log.info("Database URL: %s", DATABASE_URL)


class Base(DeclarativeBase):
    """Common declarative base for all ORM models."""


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    """Create all tables (idempotent). Called from FastAPI lifespan."""
    from . import models  # noqa: F401  ensures models register on Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database initialised (%d tables)", len(Base.metadata.tables))


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a fresh AsyncSession per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def shutdown_db() -> None:
    await engine.dispose()
    log.info("Database engine disposed")
