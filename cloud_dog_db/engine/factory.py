"""Engine factories for sync and async SQLAlchemy runtimes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from cloud_dog_db.config.models import DatabaseDialect, DatabaseSettings


def _is_sqlite(settings: DatabaseSettings) -> bool:
    url = settings.to_sync_url()
    return make_url(url).get_backend_name() == DatabaseDialect.SQLITE.value


def _base_connect_args(settings: DatabaseSettings) -> dict[str, Any]:
    connect_args: dict[str, Any] = {}
    if _is_sqlite(settings):
        connect_args["check_same_thread"] = False
    else:
        connect_args["connect_timeout"] = settings.connect_timeout_seconds
    return connect_args


def build_sync_engine(settings: DatabaseSettings) -> Engine:
    """Build a SQLAlchemy sync engine with dialect-aware defaults."""

    kwargs: dict[str, Any] = {
        "echo": settings.echo,
        "pool_pre_ping": True,
        "connect_args": _base_connect_args(settings),
    }

    if not _is_sqlite(settings):
        kwargs.update(
            {
                "pool_size": settings.pool_size,
                "max_overflow": settings.max_overflow,
                "pool_timeout": settings.pool_timeout_seconds,
                "pool_recycle": settings.pool_recycle_seconds,
            }
        )
    if settings.isolation_level:
        kwargs["isolation_level"] = settings.isolation_level

    engine = create_engine(settings.to_sync_url(), **kwargs)

    if _is_sqlite(settings):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def build_async_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Build a SQLAlchemy async engine with dialect-aware defaults."""

    kwargs: dict[str, Any] = {
        "echo": settings.echo,
        "pool_pre_ping": True,
        "connect_args": _base_connect_args(settings),
    }

    if not _is_sqlite(settings):
        kwargs.update(
            {
                "pool_size": settings.pool_size,
                "max_overflow": settings.max_overflow,
                "pool_timeout": settings.pool_timeout_seconds,
                "pool_recycle": settings.pool_recycle_seconds,
            }
        )
    if settings.isolation_level:
        kwargs["isolation_level"] = settings.isolation_level

    engine = create_async_engine(settings.to_async_url(), **kwargs)

    if _is_sqlite(settings):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def dialect_name(url: str) -> str:
    return make_url(url).get_backend_name()
