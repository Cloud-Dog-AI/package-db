"""NoSQL / search / wide-column / vector connection settings (FR.NS.7).

`NoSqlSettings` mirrors :class:`cloud_dog_db.config.models.DatabaseSettings` for the
NoSQL surface. Credentials are resolved by the caller through ``cloud_dog_config``
(Vault) and handed to :meth:`NoSqlSettings.from_provider` as a resolved provider
mapping (e.g. ``dev.databases.providers.mongodb``). No direct ``os.environ.get``
is used outside the bootstrap carve-out.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator

from cloud_dog_db.config.models import NOSQL_DIALECTS, DatabaseDialect

#: Default ports per dialect when a provider omits one.
_DEFAULT_PORTS: dict[DatabaseDialect, int] = {
    DatabaseDialect.MONGODB: 27017,
    DatabaseDialect.COUCHDB: 5984,
    DatabaseDialect.ELASTICSEARCH: 9200,
    DatabaseDialect.OPENSEARCH: 9200,
    DatabaseDialect.CASSANDRA: 9042,
    DatabaseDialect.PGVECTOR: 5432,
}


class NoSqlSettings(BaseModel):
    """Dialect-agnostic settings for the NoSQL/search/wide-column/vector clients."""

    dialect: DatabaseDialect
    host: str = "localhost"
    port: int | None = None
    username: str | None = None
    password: SecretStr | None = None
    database: str = ""
    use_ssl: bool = False
    verify_certs: bool = False
    timeout_seconds: int = 15
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("dialect", mode="before")
    @classmethod
    def _coerce_dialect(cls, value: Any) -> Any:
        if isinstance(value, DatabaseDialect):
            return value
        text = str(value).strip().lower()
        for member in DatabaseDialect:
            if member.value == text or member.name.lower() == text:
                return member
        aliases = {
            "mongo": DatabaseDialect.MONGODB,
            "couch": DatabaseDialect.COUCHDB,
            "elastic": DatabaseDialect.ELASTICSEARCH,
            "es": DatabaseDialect.ELASTICSEARCH,
            "os": DatabaseDialect.OPENSEARCH,
            "scylla": DatabaseDialect.CASSANDRA,
            "vector": DatabaseDialect.PGVECTOR,
        }
        return aliases.get(text, value)

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        if self.dialect not in NOSQL_DIALECTS:
            raise ValueError(f"{self.dialect} is not a NoSQL/vector dialect")
        if self.port is None:
            object.__setattr__(self, "port", _DEFAULT_PORTS.get(self.dialect))

    def password_plain(self) -> str | None:
        return self.password.get_secret_value() if self.password else None

    def contact_points(self) -> list[str]:
        """Cassandra contact points (comma-separated host string supported)."""
        return [h.strip() for h in self.host.split(",") if h.strip()]

    def http_scheme(self) -> str:
        return "https" if self.use_ssl else "http"

    def base_http_url(self) -> str:
        return f"{self.http_scheme()}://{self.host}:{self.port}"

    @classmethod
    def from_provider(cls, provider: dict[str, Any], **overrides: Any) -> "NoSqlSettings":
        """Build settings from a resolved Vault provider mapping.

        ``provider`` is e.g. ``read_vault_config()['dev']['databases']['providers']['mongodb']``
        with keys ``host``/``port``/``username``/``password``/``type``.
        """
        dialect = overrides.pop("dialect", None) or provider.get("type") or provider.get("dialect")
        data: dict[str, Any] = {
            "dialect": dialect,
            "host": provider.get("host", "localhost"),
            "port": provider.get("port"),
            "username": provider.get("username"),
            "password": provider.get("password"),
            "database": provider.get("database", provider.get("keyspace", "")),
            "use_ssl": _as_bool(provider.get("use_ssl", provider.get("ssl", False))),
            "verify_certs": _as_bool(provider.get("verify_certs", False)),
        }
        data.update(overrides)
        return cls.model_validate({k: v for k, v in data.items() if v is not None})

    @classmethod
    def from_env(cls, prefix: str = "CLOUD_DOG_NOSQL__") -> "NoSqlSettings":
        mapping = {
            "DIALECT": "dialect",
            "HOST": "host",
            "PORT": "port",
            "USERNAME": "username",
            "PASSWORD": "password",
            "DATABASE": "database",
            "USE_SSL": "use_ssl",
            "VERIFY_CERTS": "verify_certs",
            "TIMEOUT_SECONDS": "timeout_seconds",
        }
        data: dict[str, Any] = {}
        for suffix, key in mapping.items():
            env_key = f"{prefix}{suffix}"
            if env_key in os.environ and os.environ[env_key] != "":
                value = os.environ[env_key]
                data[key] = _as_bool(value) if key in {"use_ssl", "verify_certs"} else value
        return cls.model_validate(data)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
