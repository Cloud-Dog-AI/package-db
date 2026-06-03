# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Description: Cassandra adapter implementing the db-mcp-server connector contract.
# Related requirements: CN-01, CD-02, SC-01, CO-01, CO-02, RL-01
# Covers: CN-05
# Related tests: UT1.17, ST1.13, IT1.12
# Recent changes:
#   W28A-274-G — Initial implementation

"""Cassandra connector adapter for db-mcp-server.

Cassandra is a wide-column store with CQL (Cassandra Query Language).
Key differences from document/search stores:

* **Strongly typed schema** — ``system_schema.columns`` is authoritative.
* **Partition key + clustering columns** define data model and query constraints.
* **Limited ad-hoc filtering** — ``WHERE`` is restricted to partition/clustering
  keys unless ``ALLOW FILTERING`` is used (expensive full-table scan).
* **No joins** — relationships are inferred from field names only.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import Cluster, Session


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


_SYSTEM_KEYSPACES = frozenset({
    "system",
    "system_auth",
    "system_distributed",
    "system_schema",
    "system_traces",
    "system_virtual_schema",
    "system_views",
})


class CassandraConnector:
    """Cassandra adapter implementing the connector contract.

    Uses the ``cassandra-driver`` native protocol client. Keyspaces map to
    namespaces and tables map to entities.

    Args:
        host: Cassandra contact point hostname.
        port: Native protocol port (default 9042).
        username: Optional authentication username.
        password: Optional authentication password.
        timeout_seconds: Default request timeout.

    Related tests:
        UT1.17, ST1.13, IT1.12
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 9042,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_seconds
        auth: PlainTextAuthProvider | None = None
        if username:
            auth = PlainTextAuthProvider(
                username=username, password=password or ""
            )
        self._cluster = Cluster(
            [host], port=port, auth_provider=auth,
            connect_timeout=timeout_seconds,
        )
        self._session: Session = self._cluster.connect()
        self._session.default_timeout = float(timeout_seconds)

    @classmethod
    def from_uri(cls, uri: str, *, timeout_seconds: int = 30) -> "CassandraConnector":
        """Construct from a ``cassandra://[user:pass@]host[:port]`` URI.

        Args:
            uri: Connection URI.
            timeout_seconds: Request timeout.

        Returns:
            Configured CassandraConnector instance.
        """
        parsed = urlparse(uri)
        return cls(
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 9042,
            username=parsed.username,
            password=parsed.password,
            timeout_seconds=timeout_seconds,
        )

    def capability_report(self) -> dict[str, Any]:
        """Return the Cassandra adapter capability set.

        Returns:
            Dictionary describing which connector operations are supported.
            Note: ``structured_read`` is limited by partition key constraints.
        """
        return {
            "source_type": "cassandra",
            "supports": {
                "namespace_listing": True,
                "entity_listing": True,
                "entity_description": True,
                "field_inference": False,
                "mapping_schema": True,
                "structured_read": True,
                "create": True,
                "update": True,
                "delete": True,
                "count": True,
                "sample_shapes": True,
                "list_indexes": True,
                "schema_change_plan": True,
                "schema_change_apply": True,
                "relationship_inference": True,
                "content_search": False,
            },
            "schema_change_operations": [
                "create_entity",
                "drop_entity",
                "create_index",
                "drop_index",
            ],
            "notes": [
                "CQL WHERE clauses are restricted to partition/clustering keys.",
                "Unfiltered reads use ALLOW FILTERING (expensive on large tables).",
                "content_search is not supported — Cassandra has no full-text search.",
            ],
            "timestamp": _utcnow(),
        }

    def validate_profile(self) -> dict[str, Any]:
        """Validate connectivity to the Cassandra cluster.

        Returns:
            Validation result with cluster name.

        Raises:
            RuntimeError: If the cluster does not respond.
        """
        row = self._session.execute(
            "SELECT cluster_name FROM system.local"
        ).one()
        if not row:
            raise RuntimeError("Cassandra cluster did not return local info")
        return {
            "ok": True,
            "source_type": "cassandra",
            "cluster_name": row.cluster_name,
            "timestamp": _utcnow(),
        }

    def list_namespaces(self) -> list[dict[str, Any]]:
        """List non-system keyspaces.

        Returns:
            List of keyspace namespace descriptors.
        """
        rows = self._session.execute(
            "SELECT keyspace_name, replication FROM system_schema.keyspaces"
        )
        return [
            {
                "name": row.keyspace_name,
                "type": "keyspace",
                "replication": dict(row.replication) if row.replication else {},
            }
            for row in rows
            if row.keyspace_name not in _SYSTEM_KEYSPACES
        ]

    def list_entities(self, namespace: str) -> list[dict[str, Any]]:
        """List tables within a keyspace.

        Args:
            namespace: Keyspace name.

        Returns:
            List of table entity descriptors.
        """
        self._ensure_namespace(namespace)
        rows = self._session.execute(
            "SELECT table_name FROM system_schema.tables WHERE keyspace_name = %s",
            (namespace,),
        )
        return [{"name": row.table_name, "type": "table"} for row in rows]

    def describe_entity(self, namespace: str, entity: str) -> dict[str, Any]:
        """Describe table metadata including column schema and row count.

        Args:
            namespace: Keyspace name.
            entity: Table name.

        Returns:
            Entity metadata.
        """
        self._ensure_namespace(namespace)
        fields = self.describe_fields(namespace, entity)
        return {
            "namespace": namespace,
            "entity": entity,
            "entity_type": "table",
            "document_count": self.count(namespace, entity),
            "fields": fields["fields"],
            "indexes": self.list_indexes(namespace, entity),
        }

    def describe_fields(self, namespace: str, entity: str) -> dict[str, Any]:
        """Return CQL column metadata for a table.

        Args:
            namespace: Keyspace name.
            entity: Table name.

        Returns:
            Field descriptors with CQL types and key roles (partition_key,
            clustering, regular, static).
        """
        self._ensure_namespace(namespace)
        rows = self._session.execute(
            "SELECT column_name, type, kind, position "
            "FROM system_schema.columns "
            "WHERE keyspace_name = %s AND table_name = %s",
            (namespace, entity),
        )
        fields = []
        for row in rows:
            fields.append({
                "name": row.column_name,
                "types": [row.type],
                "kind": row.kind,
                "position": row.position,
            })
        fields.sort(key=lambda f: (
            0 if f["kind"] == "partition_key" else
            1 if f["kind"] == "clustering" else
            2 if f["kind"] == "static" else 3,
            f.get("position", 0),
            f["name"],
        ))
        return {
            "namespace": namespace,
            "entity": entity,
            "fields": fields,
            "sample_count": 0,
            "source": "system_schema",
        }

    def read(
        self,
        namespace: str,
        entity: str,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read rows from a Cassandra table.

        Args:
            namespace: Keyspace name.
            entity: Table name.
            filter: Optional structured filter (translated to CQL WHERE).
            projection: Optional field inclusion map.
            sort: Ignored — Cassandra sort is determined by clustering order.
            limit: Maximum rows to return.

        Returns:
            List of row dicts.
        """
        self._ensure_namespace(namespace)
        columns = self._projection_columns(projection) or "*"
        where, params = self._build_where(filter)
        allow_filtering = " ALLOW FILTERING" if where and not self._is_partition_key_filter(namespace, entity, filter) else ""
        effective_limit = int(limit or 50)
        cql = f"SELECT {columns} FROM {namespace}.{entity}{where} LIMIT {effective_limit}{allow_filtering}"
        rows = self._session.execute(cql, params if params else None)
        return [self._row_to_dict(row) for row in rows]

    def create(self, namespace: str, entity: str, document: dict[str, Any]) -> dict[str, Any]:
        """Insert a row into a Cassandra table.

        Args:
            namespace: Keyspace name.
            entity: Table name.
            document: Row data as a dict.

        Returns:
            Result with the inserted document.
        """
        self._ensure_namespace(namespace)
        payload = dict(document or {})
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["%s"] * len(payload))
        cql = f"INSERT INTO {namespace}.{entity} ({columns}) VALUES ({placeholders})"
        self._session.execute(cql, list(payload.values()))
        pk_filter = self._extract_pk_filter(namespace, entity, payload)
        if pk_filter:
            inserted = self.read(namespace, entity, pk_filter, limit=1)
            if inserted:
                return {"inserted_id": str(payload.get("_id", "")), "document": inserted[0]}
        return {"inserted_id": str(payload.get("_id", "")), "document": payload}

    def update(
        self,
        namespace: str,
        entity: str,
        filter: dict[str, Any],
        update: dict[str, Any],
    ) -> dict[str, Any]:
        """Update rows matching a filter.

        Args:
            namespace: Keyspace name.
            entity: Table name.
            filter: Filter to identify rows (should include partition key).
            update: Update specification with ``$set`` and/or ``$unset`` keys.

        Returns:
            Counts of matched and modified rows.
        """
        self._ensure_namespace(namespace)
        set_values = dict((update or {}).get("$set") or {})
        unset_fields = (update or {}).get("$unset") or []
        if isinstance(unset_fields, dict):
            unset_fields = list(unset_fields)

        if not set_values and not unset_fields:
            raise ValueError("Cassandra update requires $set and/or $unset")

        where, where_params = self._build_where(filter)
        if not where:
            raise ValueError("Cassandra UPDATE requires a WHERE clause")

        set_parts = []
        set_params: list[Any] = []
        for key, value in set_values.items():
            set_parts.append(f"{key} = %s")
            set_params.append(value)
        for field in unset_fields:
            set_parts.append(f"{field} = null")

        cql = f"UPDATE {namespace}.{entity} SET {', '.join(set_parts)}{where}"
        self._session.execute(cql, set_params + (where_params or []))
        return {"matched_count": 1, "modified_count": 1}

    def delete(self, namespace: str, entity: str, filter: dict[str, Any]) -> dict[str, Any]:
        """Delete rows matching a filter.

        Args:
            namespace: Keyspace name.
            entity: Table name.
            filter: Filter identifying rows (must include partition key).

        Returns:
            Count of deleted rows.
        """
        self._ensure_namespace(namespace)
        where, params = self._build_where(filter)
        if not where:
            raise ValueError("Cassandra DELETE requires a WHERE clause")
        cql = f"DELETE FROM {namespace}.{entity}{where}"
        self._session.execute(cql, params if params else None)
        return {"deleted_count": 1}

    def count(self, namespace: str, entity: str, filter: dict[str, Any] | None = None) -> int:
        """Count rows in a table.

        Args:
            namespace: Keyspace name.
            entity: Table name.
            filter: Optional filter clause.

        Returns:
            Number of matching rows.
        """
        self._ensure_namespace(namespace)
        where, params = self._build_where(filter)
        allow_filtering = " ALLOW FILTERING" if where and not self._is_partition_key_filter(namespace, entity, filter) else ""
        cql = f"SELECT COUNT(*) FROM {namespace}.{entity}{where}{allow_filtering}"
        row = self._session.execute(cql, params if params else None).one()
        return int(row.count) if row else 0

    def sample_shapes(self, namespace: str, entity: str, n: int = 10) -> list[dict[str, Any]]:
        """Sample rows from a table.

        Args:
            namespace: Keyspace name.
            entity: Table name.
            n: Maximum number of samples.

        Returns:
            List of row dicts.
        """
        return self.read(namespace, entity, limit=max(1, n))

    def list_indexes(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        """List secondary indexes on a table.

        Args:
            namespace: Keyspace name.
            entity: Table name.

        Returns:
            List of index descriptors.
        """
        self._ensure_namespace(namespace)
        rows = self._session.execute(
            "SELECT index_name, kind, options "
            "FROM system_schema.indexes "
            "WHERE keyspace_name = %s AND table_name = %s",
            (namespace, entity),
        )
        return [
            {
                "name": row.index_name,
                "type": f"secondary_index ({row.kind})",
                "options": dict(row.options) if row.options else {},
            }
            for row in rows
        ]

    def schema_change_plan(self, operation: dict[str, Any]) -> dict[str, Any]:
        """Create a dry-run schema change plan.

        Args:
            operation: Schema change specification.

        Returns:
            Dry-run plan.

        Raises:
            ValueError: For unsupported operations.
        """
        raw = dict(operation or {})
        op_type = str(raw.get("operation") or raw.get("op_type") or "").strip()
        namespace = str(raw.get("namespace") or "").strip()
        entity = str(raw.get("entity") or "").strip()
        parameters = dict(raw.get("parameters") or {})
        for key, value in raw.items():
            if key not in {"operation", "op_type", "namespace", "entity", "parameters"}:
                parameters[key] = value

        if op_type == "create_entity":
            return self._plan_create_entity(namespace, entity, parameters)
        if op_type == "drop_entity":
            return self._plan_drop_entity(namespace, entity, parameters)
        if op_type == "create_index":
            return self._plan_create_index(namespace, entity, parameters)
        if op_type == "drop_index":
            return self._plan_drop_index(namespace, entity, parameters)
        raise ValueError(f"Unsupported schema change operation: {op_type}")

    def schema_change_apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Apply a previously planned schema change.

        Args:
            plan: Plan dictionary.

        Returns:
            Result indicating success.

        Raises:
            ValueError: For unsupported operations.
        """
        raw = dict(plan or {})
        op_type = str(raw.get("operation") or raw.get("op_type") or "").strip()
        namespace = str(raw.get("namespace") or "").strip()
        entity = str(raw.get("entity") or "").strip()
        parameters = dict(raw.get("parameters") or {})
        for key, value in raw.items():
            if key not in {"operation", "op_type", "namespace", "entity", "parameters"}:
                parameters[key] = value

        if op_type == "create_entity":
            columns = dict(parameters.get("columns") or {"id": "text"})
            pk = str(parameters.get("primary_key") or list(columns)[0])
            col_defs = ", ".join(f"{name} {cql_type}" for name, cql_type in columns.items())
            cql = f"CREATE TABLE IF NOT EXISTS {namespace}.{entity} ({col_defs}, PRIMARY KEY ({pk}))"
            self._session.execute(cql)
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_created": True,
            }
        if op_type == "drop_entity":
            self._session.execute(f"DROP TABLE IF EXISTS {namespace}.{entity}")
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_dropped": True,
            }
        if op_type == "create_index":
            column = str(parameters.get("column") or "").strip()
            index_name = str(parameters.get("name") or f"idx_{entity}_{column}").strip()
            if not column:
                raise ValueError("create_index requires a 'column' parameter")
            cql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {namespace}.{entity} ({column})"
            self._session.execute(cql)
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "index_name": index_name,
            }
        if op_type == "drop_index":
            index_name = str(parameters.get("name") or "").strip()
            if not index_name:
                raise ValueError("drop_index requires a 'name' parameter")
            cql = f"DROP INDEX IF EXISTS {namespace}.{index_name}"
            self._session.execute(cql)
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "index_name": index_name,
            }
        raise ValueError(f"Unsupported schema change operation: {op_type}")

    def extract_relationships(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        """Infer relationship candidates from column names ending in ``_id``.

        Args:
            namespace: Keyspace name.
            entity: Table name.

        Returns:
            List of inferred relationship descriptors.
        """
        self._ensure_namespace(namespace)
        fields = self.describe_fields(namespace, entity)["fields"]
        relationships: list[dict[str, Any]] = []
        for field in fields:
            name = field["name"]
            if name.endswith("_id") and name != "_id":
                relationships.append({
                    "field": name,
                    "relationship_type": "reference_candidate",
                    "target_entity_hint": f"{name[:-3]}s",
                })
        return relationships

    def _plan_create_entity(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        existing = [e["name"] for e in self.list_entities(namespace)]
        return {
            "dry_run": True,
            "operation": "create_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "before_state": {"entity_exists": entity in existing},
            "after_state": {"entity_exists": True},
        }

    def _plan_drop_entity(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        return {
            "dry_run": True,
            "operation": "drop_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "before_state": {"entity_exists": True},
            "after_state": {"entity_exists": False},
        }

    def _plan_create_index(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        column = str(parameters.get("column") or "").strip()
        index_name = str(parameters.get("name") or f"idx_{entity}_{column}").strip()
        before = [item["name"] for item in self.list_indexes(namespace, entity)]
        return {
            "dry_run": True,
            "operation": "create_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "before_state": {"indexes": before},
            "after_state": {"indexes": sorted(set(before + [index_name]))},
        }

    def _plan_drop_index(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        index_name = str(parameters.get("name") or "").strip()
        before = [item["name"] for item in self.list_indexes(namespace, entity)]
        after = [item for item in before if item != index_name]
        return {
            "dry_run": True,
            "operation": "drop_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": {"name": index_name, **parameters},
            "before_state": {"indexes": before},
            "after_state": {"indexes": after},
        }

    def close(self) -> None:
        """Close the Cassandra session and cluster connection."""
        try:
            self._session.shutdown()
        except Exception:
            pass
        try:
            self._cluster.shutdown()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_namespace(self, namespace: str) -> None:
        """Ensure the keyspace exists, creating it if missing (lazy auto-create)."""
        if not namespace:
            raise ValueError("Namespace (keyspace) is required for Cassandra operations")
        rows = self._session.execute(
            "SELECT keyspace_name FROM system_schema.keyspaces WHERE keyspace_name = %s",
            (namespace,),
        )
        if not rows.one():
            self._session.execute(
                f"CREATE KEYSPACE IF NOT EXISTS {namespace} "
                "WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}"
            )

    def _get_partition_keys(self, namespace: str, entity: str) -> list[str]:
        """Return the partition key column names for a table."""
        rows = self._session.execute(
            "SELECT column_name, kind FROM system_schema.columns "
            "WHERE keyspace_name = %s AND table_name = %s",
            (namespace, entity),
        )
        return [row.column_name for row in rows if row.kind == "partition_key"]

    def _is_partition_key_filter(
        self, namespace: str, entity: str, filter: dict[str, Any] | None
    ) -> bool:
        """Check whether a filter covers the partition key columns."""
        if not filter:
            return False
        pk_columns = set(self._get_partition_keys(namespace, entity))
        filter_columns = set(filter.keys()) if isinstance(filter, dict) else set()
        return pk_columns.issubset(filter_columns)

    def _extract_pk_filter(
        self, namespace: str, entity: str, document: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Build a filter from the partition key values in a document."""
        pk_columns = self._get_partition_keys(namespace, entity)
        if not pk_columns:
            return None
        result = {}
        for col in pk_columns:
            if col in document:
                result[col] = document[col]
            else:
                return None
        return result

    @staticmethod
    def _build_where(
        filter: dict[str, Any] | None,
    ) -> tuple[str, list[Any] | None]:
        """Translate a flat key-value filter to a CQL WHERE clause.

        Args:
            filter: Dict mapping column names to equality values.

        Returns:
            Tuple of (WHERE clause string, parameter list).
        """
        if not filter:
            return "", None
        conditions = []
        params: list[Any] = []
        for key, value in filter.items():
            conditions.append(f"{key} = %s")
            params.append(value)
        return " WHERE " + " AND ".join(conditions), params

    @staticmethod
    def _projection_columns(projection: dict[str, Any] | None) -> str:
        """Translate a projection dict to a CQL column list."""
        if not projection:
            return ""
        include = [key for key, value in projection.items() if int(value or 0) > 0]
        if include:
            return ", ".join(include)
        return ""

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a cassandra-driver Row to a plain dict."""
        if hasattr(row, "_asdict"):
            result = row._asdict()
        elif hasattr(row, "__dict__"):
            result = dict(row.__dict__)
        else:
            result = dict(row)
        for key, value in result.items():
            result[key] = CassandraConnector._normalise_value(value)
        return result

    @staticmethod
    def _normalise_value(value: Any) -> Any:
        """Convert Cassandra-specific scalar values into JSON-safe primitives."""
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: CassandraConnector._normalise_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [CassandraConnector._normalise_value(item) for item in value]
        if hasattr(value, "isoformat") and callable(value.isoformat):
            try:
                return value.isoformat()
            except Exception:
                pass
        if value.__class__.__name__ == "Date":
            return str(value)
        return value
