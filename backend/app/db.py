"""SQLAlchemy engine/session setup.

Supports both Postgres (docker-compose, Render) and SQLite (zero-dependency
local / free-tier fallback) via the DATABASE_URL. Render historically hands
out `postgres://` URLs which SQLAlchemy needs as `postgresql://`.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

url = settings.DATABASE_URL
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_session():
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
