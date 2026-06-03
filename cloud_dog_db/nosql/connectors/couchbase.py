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
# Description: Couchbase adapter implementing the shared db-mcp-server connector contract.
# Related requirements: CN-01, CN-02
# Related tests: UT1.16, ST1.12, IT1.10

"""Couchbase connector adapter for db-mcp-server."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, ClusterTimeoutOptions, QueryOptions
from couchbase.n1ql import QueryScanConsistency
from couchbase.exceptions import (
    CouchbaseException,
    BucketNotFoundException,
    DocumentNotFoundException,
    ScopeNotFoundException,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CouchbaseConnector:
    """Couchbase adapter implementing the connector contract."""

    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        port: int = 8091,
        timeout_seconds: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._timeout_seconds = timeout_seconds
        conn_str = f"couchbase://{host}"
        auth = PasswordAuthenticator(username, password)
        timeout_opts = ClusterTimeoutOptions(
            kv_timeout=timedelta(seconds=timeout_seconds),
            query_timeout=timedelta(seconds=timeout_seconds),
        )
        self._cluster = Cluster(conn_str, ClusterOptions(auth, timeout_options=timeout_opts))
        self._cluster.wait_until_ready(timedelta(seconds=timeout_seconds))

    @classmethod
    def from_uri(cls, uri: str, *, timeout_seconds: int = 30) -> "CouchbaseConnector":
        parsed = urlsplit(uri)
        return cls(
            host=parsed.hostname or "localhost",
            port=parsed.port or 8091,
            username=parsed.username or "Administrator",
            password=parsed.password or "",
            timeout_seconds=timeout_seconds,
        )

    def close(self) -> None:
        pass

    @property
    def uri(self) -> str:
        return f"couchbase://{self._host}"

    def capability_report(self) -> dict[str, Any]:
        return {
            "source_type": "couchbase",
            "supports": {
                "namespace_listing": True,
                "entity_listing": True,
                "entity_description": True,
                "field_inference": True,
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
            },
            "schema_change_operations": ["create_index", "drop_index"],
            "timestamp": _utcnow(),
        }

    def validate_profile(self) -> dict[str, Any]:
        try:
            mgr = self._cluster.buckets()
            buckets = mgr.get_all_buckets()
            return {
                "ok": True,
                "source_type": "couchbase",
                "bucket_count": len(buckets),
                "timestamp": _utcnow(),
            }
        except CouchbaseException as exc:
            return {"ok": False, "source_type": "couchbase", "error": str(exc), "timestamp": _utcnow()}

    def list_namespaces(self) -> list[dict[str, Any]]:
        mgr = self._cluster.buckets()
        buckets = mgr.get_all_buckets()
        return [{"name": b.name, "type": "bucket"} for b in buckets]

    def list_entities(self, namespace: str) -> list[dict[str, Any]]:
        bucket = self._cluster.bucket(namespace)
        try:
            scopes = bucket.collections().get_all_scopes()
        except Exception:
            return [{"name": "_default", "type": "collection", "namespace": namespace}]
        entities = []
        for scope in scopes:
            for coll in scope.collections:
                entities.append({
                    "name": f"{scope.name}.{coll.name}" if scope.name != "_default" else coll.name,
                    "type": "collection",
                    "namespace": namespace,
                    "scope": scope.name,
                    "collection": coll.name,
                })
        return entities

    def describe_entity(self, namespace: str, entity: str) -> dict[str, Any]:
        scope_name, coll_name = self._parse_entity(entity)
        sample = self.sample_shapes(namespace, entity, n=20)
        field_types: dict[str, set[str]] = defaultdict(set)
        for doc in sample:
            for key, val in doc.items():
                field_types[key].add(type(val).__name__)
        fields = [
            {"name": k, "type": ", ".join(sorted(v)), "nullable": True, "indexed": False}
            for k, v in sorted(field_types.items())
        ]
        return {
            "namespace": namespace,
            "entity": entity,
            "scope": scope_name,
            "collection": coll_name,
            "fields": fields,
            "field_count": len(fields),
            "sample_count": len(sample),
            "timestamp": _utcnow(),
        }

    def describe_fields(self, namespace: str, entity: str) -> dict[str, Any]:
        desc = self.describe_entity(namespace, entity)
        return {"fields": desc["fields"], "field_count": desc["field_count"]}

    def read(
        self,
        namespace: str,
        entity: str,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        scope_name, coll_name = self._parse_entity(entity)
        keyspace = f"`{namespace}`.`{scope_name}`.`{coll_name}`"
        where_clause, params = self._build_where(filter)
        query = f"SELECT META().id AS _id, d.* FROM {keyspace} AS d"
        if where_clause:
            query += f" WHERE {where_clause}"
        if sort:
            order_parts = []
            for s in sort:
                field = s.get("field", "")
                direction = "ASC" if s.get("direction", "asc").lower() == "asc" else "DESC"
                order_parts.append(f"d.`{field}` {direction}")
            if order_parts:
                query += f" ORDER BY {', '.join(order_parts)}"
        if limit and limit > 0:
            query += f" LIMIT {int(limit)}"
        result = self._cluster.query(
            query, QueryOptions(named_parameters=params, scan_consistency=QueryScanConsistency.REQUEST_PLUS)
        )
        return [dict(row) for row in result]

    def create(self, namespace: str, entity: str, document: dict[str, Any]) -> dict[str, Any]:
        scope_name, coll_name = self._parse_entity(entity)
        bucket = self._cluster.bucket(namespace)
        scope = bucket.scope(scope_name)
        coll = scope.collection(coll_name)
        doc_id = document.pop("_id", str(uuid4()))
        coll.upsert(doc_id, document)
        return {"ok": True, "id": doc_id, "timestamp": _utcnow()}

    def update(self, namespace: str, entity: str, filter: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        scope_name, coll_name = self._parse_entity(entity)
        set_parts = []
        params: dict[str, Any] = {}
        for i, (key, val) in enumerate(update.items()):
            pname = f"$upd_{i}"
            set_parts.append(f"d.`{key}` = {pname}")
            params[f"upd_{i}"] = val
        if not set_parts:
            return {"ok": True, "modified_count": 0, "timestamp": _utcnow()}
        keyspace = f"`{namespace}`.`{scope_name}`.`{coll_name}`"
        where_clause, where_params = self._build_where(filter)
        params.update(where_params)
        query = f"UPDATE {keyspace} AS d SET {', '.join(set_parts)}"
        if where_clause:
            query += f" WHERE {where_clause}"
        result = self._cluster.query(query, QueryOptions(named_parameters=params, metrics=True))
        # SDK 4.x: the result must be consumed before metadata()/metrics() is available.
        for _ in result:
            pass
        meta = result.metadata()
        metrics = meta.metrics() if meta is not None else None
        count = int(metrics.mutation_count()) if metrics else 0
        return {"ok": True, "modified_count": count, "timestamp": _utcnow()}

    def delete(self, namespace: str, entity: str, filter: dict[str, Any]) -> dict[str, Any]:
        scope_name, coll_name = self._parse_entity(entity)
        keyspace = f"`{namespace}`.`{scope_name}`.`{coll_name}`"
        where_clause, params = self._build_where(filter)
        query = f"DELETE FROM {keyspace} AS d"
        if where_clause:
            query += f" WHERE {where_clause}"
        result = self._cluster.query(query, QueryOptions(named_parameters=params, metrics=True))
        # SDK 4.x: the result must be consumed before metadata()/metrics() is available.
        for _ in result:
            pass
        meta = result.metadata()
        metrics = meta.metrics() if meta is not None else None
        count = int(metrics.mutation_count()) if metrics else 0
        return {"ok": True, "deleted_count": count, "timestamp": _utcnow()}

    def count(self, namespace: str, entity: str, filter: dict[str, Any] | None = None) -> int:
        scope_name, coll_name = self._parse_entity(entity)
        keyspace = f"`{namespace}`.`{scope_name}`.`{coll_name}`"
        where_clause, params = self._build_where(filter)
        query = f"SELECT COUNT(*) AS cnt FROM {keyspace} AS d"
        if where_clause:
            query += f" WHERE {where_clause}"
        result = self._cluster.query(
            query, QueryOptions(named_parameters=params, scan_consistency=QueryScanConsistency.REQUEST_PLUS)
        )
        for row in result:
            return int(row.get("cnt", 0))
        return 0

    def sample_shapes(self, namespace: str, entity: str, n: int = 10) -> list[dict[str, Any]]:
        scope_name, coll_name = self._parse_entity(entity)
        keyspace = f"`{namespace}`.`{scope_name}`.`{coll_name}`"
        query = f"SELECT d.* FROM {keyspace} AS d LIMIT {int(n)}"
        result = self._cluster.query(query, QueryOptions(scan_consistency=QueryScanConsistency.REQUEST_PLUS))
        return [dict(row) for row in result]

    def list_indexes(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        scope_name, coll_name = self._parse_entity(entity)
        query = (
            "SELECT idx.* FROM system:indexes AS idx "
            f"WHERE idx.keyspace_id = '{coll_name}' "
            f"AND idx.bucket_id = '{namespace}'"
        )
        try:
            result = self._cluster.query(query)
            return [dict(row) for row in result]
        except CouchbaseException:
            return []

    def schema_change_plan(self, operation: dict[str, Any]) -> dict[str, Any]:
        op_type = operation.get("type", "")
        namespace = operation.get("namespace", "")
        entity = operation.get("entity", "")
        scope_name, coll_name = self._parse_entity(entity)
        if op_type == "create_index":
            index_name = operation.get("index_name", "")
            fields = operation.get("fields", [])
            field_exprs = ", ".join(f"d.`{f}`" for f in fields)
            keyspace = f"`{namespace}`.`{scope_name}`.`{coll_name}`"
            return {
                "ok": True,
                "plan": f"CREATE INDEX `{index_name}` ON {keyspace}({field_exprs})",
                "reversible": True,
                "reverse": f"DROP INDEX {keyspace}.`{index_name}`",
            }
        if op_type == "drop_index":
            index_name = operation.get("index_name", "")
            keyspace = f"`{namespace}`.`{scope_name}`.`{coll_name}`"
            return {
                "ok": True,
                "plan": f"DROP INDEX {keyspace}.`{index_name}`",
                "reversible": False,
            }
        return {"ok": False, "error": f"Unsupported operation: {op_type}"}

    def schema_change_apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        statement = plan.get("plan", "")
        if not statement:
            return {"ok": False, "error": "Empty plan"}
        try:
            self._cluster.query(statement)
            return {"ok": True, "applied": statement, "timestamp": _utcnow()}
        except CouchbaseException as exc:
            return {"ok": False, "error": str(exc), "timestamp": _utcnow()}

    def extract_relationships(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        desc = self.describe_entity(namespace, entity)
        fields = desc.get("fields", [])
        candidates = []
        for field in fields:
            name = field.get("name", "")
            if name.endswith("_id") or name.endswith("_ref") or name.endswith("Id"):
                target_entity = name.replace("_id", "").replace("_ref", "").replace("Id", "")
                candidates.append({
                    "source_entity": entity,
                    "target_entity": target_entity,
                    "source_field": name,
                    "target_field": "_id",
                    "type": "many-to-one",
                    "confidence": 0.6,
                    "method": "field_name_pattern",
                })
        return candidates

    def _parse_entity(self, entity: str) -> tuple[str, str]:
        if "." in entity:
            parts = entity.split(".", 1)
            return parts[0], parts[1]
        return "_default", entity

    @staticmethod
    def _col_ref(key: str) -> str:
        # ``_id`` is the document key (META().id), exposed by read() as ``_id`` —
        # it is not a stored field, so it must be addressed via META() to make
        # documents filterable/updatable/deletable by the id read() returns.
        return "META(d).id" if key == "_id" else f"d.`{key}`"

    def _build_where(self, filter: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        if not filter:
            return "", {}
        params: dict[str, Any] = {}
        conditions = []
        idx = 0
        for key, val in filter.items():
            ref = self._col_ref(key)
            if isinstance(val, dict):
                for op, operand in val.items():
                    pname = f"p_{idx}"
                    sql_op = {"$eq": "=", "$ne": "!=", "$gt": ">", "$lt": "<", "$gte": ">=", "$lte": "<="}.get(op, "=")
                    conditions.append(f"{ref} {sql_op} ${pname}")
                    params[pname] = operand
                    idx += 1
            else:
                pname = f"p_{idx}"
                conditions.append(f"{ref} = ${pname}")
                params[pname] = val
                idx += 1
        return " AND ".join(conditions), params
