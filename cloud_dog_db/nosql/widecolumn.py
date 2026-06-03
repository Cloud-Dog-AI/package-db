"""Wide-column repository for Cassandra (FR.NS.3).

The ``cassandra`` driver import is confined to this module and loaded lazily.
"""

from __future__ import annotations

from typing import Any

from cloud_dog_db.config.models import DatabaseDialect
from cloud_dog_db.crud.repository import DBError, RecordNotFoundError
from cloud_dog_db.crud.specs import FilterOperator, PageResult, PageSpec, QuerySpec
from cloud_dog_db.nosql.settings import NoSqlSettings

_OP_CQL = {
    FilterOperator.EQ: "=",
    FilterOperator.NE: "!=",
    FilterOperator.GT: ">",
    FilterOperator.GTE: ">=",
    FilterOperator.LT: "<",
    FilterOperator.LTE: "<=",
    FilterOperator.IN: "IN",
}


class CassandraWideColumnRepository:
    """Cassandra-backed :class:`~cloud_dog_db.nosql.protocols.WideColumnRepository`."""

    def __init__(
        self,
        settings: NoSqlSettings,
        keyspace: str,
        table: str,
        key_columns: list[str],
        *,
        session: Any = None,
    ):
        if settings.dialect != DatabaseDialect.CASSANDRA:
            raise DBError(f"CassandraWideColumnRepository requires CASSANDRA dialect, got {settings.dialect}")
        self._keyspace = keyspace
        self._table = table
        self._key_columns = key_columns
        self._owns = session is None
        if session is None:
            from cassandra.cluster import Cluster  # lazy — extra [cassandra]

            auth = None
            if settings.username:
                from cassandra.auth import PlainTextAuthProvider

                auth = PlainTextAuthProvider(username=settings.username, password=settings.password_plain())
            self._cluster = Cluster(
                contact_points=settings.contact_points(),
                port=settings.port,
                auth_provider=auth,
                connect_timeout=settings.timeout_seconds,
            )
            session = self._cluster.connect()
        else:
            self._cluster = None
        self._session = session

    @property
    def _fqtn(self) -> str:
        return f"{self._keyspace}.{self._table}"

    def create(self, row: dict[str, Any]) -> dict[str, Any]:
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cql = f"INSERT INTO {self._fqtn} ({', '.join(cols)}) VALUES ({placeholders})"
        self._session.execute(cql, tuple(row[c] for c in cols))
        return dict(row)

    def _where_key(self, key: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses = [f"{c} = %s" for c in self._key_columns]
        return " AND ".join(clauses), [key[c] for c in self._key_columns]

    def get(self, key: dict[str, Any]) -> dict[str, Any]:
        where, params = self._where_key(key)
        rows = list(self._session.execute(f"SELECT * FROM {self._fqtn} WHERE {where}", tuple(params)))
        if not rows:
            raise RecordNotFoundError(f"row({key}) not found")
        return dict(rows[0]._asdict())

    def update(self, key: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self.get(key)  # raises if missing
        set_cols = {k: v for k, v in payload.items() if k not in self._key_columns}
        set_clause = ", ".join(f"{c} = %s" for c in set_cols)
        where, key_params = self._where_key(key)
        cql = f"UPDATE {self._fqtn} SET {set_clause} WHERE {where}"
        self._session.execute(cql, tuple(list(set_cols.values()) + key_params))
        return self.get(key)

    def delete(self, key: dict[str, Any]) -> None:
        self.get(key)  # raises if missing
        where, params = self._where_key(key)
        self._session.execute(f"DELETE FROM {self._fqtn} WHERE {where}", tuple(params))

    def list(self, spec: QuerySpec | None = None) -> PageResult[dict[str, Any]]:
        page = (spec.page if spec else None) or PageSpec()
        where_parts: list[str] = []
        params: list[Any] = []
        for f in (spec.filters if spec else []):
            op = _OP_CQL.get(f.operator)
            if op is None:
                continue
            where_parts.append(f"{f.field} {op} %s")
            params.append(f.value)
        cql = f"SELECT * FROM {self._fqtn}"
        if where_parts:
            cql += " WHERE " + " AND ".join(where_parts) + " ALLOW FILTERING"
        rows = [dict(r._asdict()) for r in self._session.execute(cql, tuple(params))]
        total = len(rows)
        window = rows[page.offset : page.offset + page.limit]
        return PageResult(items=window, total=total, limit=page.limit, offset=page.offset)

    def execute(self, cql: str, params: tuple[Any, ...] = ()) -> Any:
        """Escape hatch for keyspace/table DDL in tests/setup."""
        return self._session.execute(cql, params)

    def close(self) -> None:
        if self._owns and self._cluster is not None:
            self._cluster.shutdown()


def build_wide_column_client(
    settings: NoSqlSettings,
    *,
    keyspace: str,
    table: str,
    key_columns: list[str],
) -> CassandraWideColumnRepository:
    """Factory dispatch for wide-column repositories (FR.NS.3)."""
    if settings.dialect != DatabaseDialect.CASSANDRA:
        raise DBError(f"{settings.dialect} is not a wide-column dialect")
    return CassandraWideColumnRepository(settings, keyspace=keyspace, table=table, key_columns=key_columns)
