"""Configuration models for database connectivity."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy.engine import URL, make_url


class DatabaseDialect(str, Enum):
    SQLITE = "sqlite"
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"


class DatabaseSettings(BaseModel):
    """Dialect-agnostic DB settings used by engine/session factories."""

    dialect: DatabaseDialect = DatabaseDialect.SQLITE
    driver: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: SecretStr | None = None
    database: str = ""
    path: str | None = None
    query: dict[str, str] = Field(default_factory=dict)
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout_seconds: int = 30
    pool_recycle_seconds: int = 1800
    connect_timeout_seconds: int = 10
    isolation_level: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    url: str | None = None

    @field_validator("dialect", mode="before")
    @classmethod
    def _normalise_dialect(cls, value: Any) -> Any:
        if value is None:
            return DatabaseDialect.SQLITE
        text = str(value).strip().lower()
        if text in {"postgres", "postgresql"}:
            return DatabaseDialect.POSTGRESQL
        if text in {"mariadb", "mysql"}:
            return DatabaseDialect.MYSQL
        if text == "sqlite":
            return DatabaseDialect.SQLITE
        return value

    @classmethod
    def from_env(cls, prefix: str = "CLOUD_DOG_DB__") -> "DatabaseSettings":
        data: dict[str, Any] = {}
        mapping = {
            "DIALECT": "dialect",
            "DRIVER": "driver",
            "HOST": "host",
            "PORT": "port",
            "USERNAME": "username",
            "PASSWORD": "password",
            "DATABASE": "database",
            "PATH": "path",
            "ECHO": "echo",
            "POOL_SIZE": "pool_size",
            "MAX_OVERFLOW": "max_overflow",
            "POOL_TIMEOUT_SECONDS": "pool_timeout_seconds",
            "POOL_RECYCLE_SECONDS": "pool_recycle_seconds",
            "CONNECT_TIMEOUT_SECONDS": "connect_timeout_seconds",
            "ISOLATION_LEVEL": "isolation_level",
            "SCHEMA": "schema_name",
            "URL": "url",
        }
        for suffix, key in mapping.items():
            env_key = f"{prefix}{suffix}"
            if env_key in os.environ and os.environ[env_key] != "":
                data[key] = os.environ[env_key]

        query_prefix = f"{prefix}QUERY__"
        query: dict[str, str] = {}
        for key, value in os.environ.items():
            if key.startswith(query_prefix):
                query[key[len(query_prefix) :].lower()] = value
        if query:
            data["query"] = query
        return cls.model_validate(data)

    def password_plain(self) -> str | None:
        return self.password.get_secret_value() if self.password else None

    def _driver_for_sync(self) -> str:
        if self.driver:
            return self.driver
        if self.dialect == DatabaseDialect.MYSQL:
            return "pymysql"
        if self.dialect == DatabaseDialect.POSTGRESQL:
            return "psycopg"
        return "pysqlite"

    def _driver_for_async(self) -> str:
        if self.driver:
            return self.driver
        if self.dialect == DatabaseDialect.MYSQL:
            return "aiomysql"
        if self.dialect == DatabaseDialect.POSTGRESQL:
            return "asyncpg"
        return "aiosqlite"

    def to_sync_url(self) -> str:
        if self.url:
            return str(make_url(self.url))

        if self.dialect == DatabaseDialect.SQLITE:
            sqlite_path = self.path or self.database or ":memory:"
            if sqlite_path == ":memory:":
                return "sqlite+pysqlite:///:memory:"
            path = Path(sqlite_path)
            return f"sqlite+pysqlite:///{path}"

        url = URL.create(
            drivername=f"{self.dialect.value}+{self._driver_for_sync()}",
            username=self.username,
            password=self.password_plain(),
            host=self.host,
            port=self.port,
            database=self.database,
            query=self.query,
        )
        return url.render_as_string(hide_password=False)

    def to_async_url(self) -> str:
        if self.url:
            url = make_url(self.url)
            driver = url.drivername
            if driver.startswith("sqlite+"):
                return str(url.set(drivername="sqlite+aiosqlite"))
            if driver.startswith("mysql+"):
                return str(url.set(drivername="mysql+aiomysql"))
            if driver.startswith("postgresql+"):
                return str(url.set(drivername="postgresql+asyncpg"))
            if driver == "sqlite":
                return str(url.set(drivername="sqlite+aiosqlite"))
            if driver == "mysql":
                return str(url.set(drivername="mysql+aiomysql"))
            if driver == "postgresql":
                return str(url.set(drivername="postgresql+asyncpg"))
            return str(url)

        if self.dialect == DatabaseDialect.SQLITE:
            sqlite_path = self.path or self.database or ":memory:"
            if sqlite_path == ":memory:":
                return "sqlite+aiosqlite:///:memory:"
            path = Path(sqlite_path)
            return f"sqlite+aiosqlite:///{path}"

        url = URL.create(
            drivername=f"{self.dialect.value}+{self._driver_for_async()}",
            username=self.username,
            password=self.password_plain(),
            host=self.host,
            port=self.port,
            database=self.database,
            query=self.query,
        )
        return url.render_as_string(hide_password=False)

    def masked_url(self) -> str:
        url = make_url(self.to_sync_url())
        return str(url.render_as_string(hide_password=True))
