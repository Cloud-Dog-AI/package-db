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
# Description: MongoDB adapter implementing the first db-mcp-server connector contract.
# Related requirements: CN-01, CD-02, SC-01, CO-01, CO-02, RL-01
# Related tests: UT1.4, ST1.3, IT1.2

"""MongoDB connector adapter for db-mcp-server."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import time
from typing import Any

from bson import ObjectId
from bson.binary import Binary
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MongoDBConnector:
    """MongoDB adapter implementing the connector contract."""

    _namespace_cache: dict[str, tuple[float, list[str]]] = {}
    _namespace_cache_ttl_seconds = 15.0

    def __init__(self, *, uri: str, timeout_ms: int = 60000) -> None:
        self._uri = uri
        self._timeout_ms = timeout_ms
        self._client = MongoClient(
            uri,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
        )

    @property
    def uri(self) -> str:
        """Return the configured MongoDB connection URI."""
        return self._uri

    def capability_report(self) -> dict[str, Any]:
        """Return the MongoDB adapter capability set."""
        return {
            "source_type": "mongodb",
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
            "schema_change_operations": ["create_entity", "drop_entity", "create_index", "drop_index"],
            "timestamp": _utcnow(),
        }

    def validate_profile(self) -> dict[str, Any]:
        """Validate connectivity to the configured MongoDB deployment."""
        self._client.admin.command("ping")
        return {"ok": True, "source_type": "mongodb", "timestamp": _utcnow()}

    def list_namespaces(self) -> list[dict[str, Any]]:
        """List MongoDB databases excluding internal ones."""
        internal = {"admin", "config", "local"}
        now = time.monotonic()
        cached = self._namespace_cache.get(self._uri)
        if cached and now - cached[0] <= self._namespace_cache_ttl_seconds:
            databases = cached[1]
        else:
            databases = list(self._client.list_database_names())
            self._namespace_cache[self._uri] = (now, databases)
        result = []
        for database in databases:
            if database in internal:
                continue
            result.append({"name": database, "type": "database"})
        return result

    def list_entities(self, namespace: str) -> list[dict[str, Any]]:
        """List collections within a MongoDB database."""
        db = self._client[namespace]
        return [
            {"name": item["name"], "type": item.get("type", "collection")}
            for item in db.list_collections()
        ]

    def describe_entity(self, namespace: str, entity: str) -> dict[str, Any]:
        """Describe collection metadata, validator, options, and indexes."""
        db = self._client[namespace]
        options = db.command({"listCollections": 1, "filter": {"name": entity}})
        first = (options.get("cursor", {}).get("firstBatch") or [{}])[0]
        collection = db[entity]
        return {
            "namespace": namespace,
            "entity": entity,
            "options": self._normalise_value(first.get("options", {})),
            "info": self._normalise_value(first.get("info", {})),
            "document_count": collection.count_documents({}),
            "indexes": self.list_indexes(namespace, entity),
        }

    def describe_fields(self, namespace: str, entity: str) -> dict[str, Any]:
        """Infer field types from sampled documents."""
        collection = self._client[namespace][entity]
        samples = list(collection.aggregate([{"$sample": {"size": 25}}]))
        field_types: dict[str, set[str]] = defaultdict(set)
        for sample in samples:
            for key, value in sample.items():
                field_types[key].add(self._field_type_name(value))
        return {
            "namespace": namespace,
            "entity": entity,
            "fields": [
                {"name": key, "types": sorted(values)}
                for key, values in sorted(field_types.items())
            ],
            "sample_count": len(samples),
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
        """Read documents using structured filter, projection, sort, and limit."""
        collection = self._client[namespace][entity]
        cursor = collection.find(filter or {}, projection)
        if sort:
            cursor = cursor.sort([(item["field"], ASCENDING if str(item.get("direction", "asc")).lower() == "asc" else DESCENDING) for item in sort])
        if limit:
            cursor = cursor.limit(int(limit))
        return [self._normalise_document(item) for item in cursor]

    def create(self, namespace: str, entity: str, document: dict[str, Any]) -> dict[str, Any]:
        """Insert a document into a collection."""
        collection = self._client[namespace][entity]
        result = collection.insert_one(document)
        created = collection.find_one({"_id": result.inserted_id})
        return {
            "inserted_id": str(result.inserted_id),
            "document": self._normalise_document(created or {}),
        }

    def update(self, namespace: str, entity: str, filter: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        """Update documents matching a structured filter."""
        collection = self._client[namespace][entity]
        # MongoDB requires update operators ($set, $unset, etc.).
        # If the caller passes a plain dict, wrap it in $set automatically.
        if update and not any(k.startswith("$") for k in update):
            update = {"$set": update}
        result = collection.update_many(filter or {}, update)
        return {
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
        }

    def delete(self, namespace: str, entity: str, filter: dict[str, Any]) -> dict[str, Any]:
        """Delete documents matching a structured filter."""
        collection = self._client[namespace][entity]
        result = collection.delete_many(filter or {})
        return {"deleted_count": result.deleted_count}

    def count(self, namespace: str, entity: str, filter: dict[str, Any] | None = None) -> int:
        """Count documents matching a structured filter."""
        return int(self._client[namespace][entity].count_documents(filter or {}))

    def sample_shapes(self, namespace: str, entity: str, n: int = 10) -> list[dict[str, Any]]:
        """Sample documents for shape and field inference."""
        collection = self._client[namespace][entity]
        cursor = collection.aggregate([{"$sample": {"size": int(max(1, n))}}])
        return [self._normalise_document(item) for item in cursor]

    def list_indexes(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        """Return collection index definitions."""
        collection = self._client[namespace][entity]
        result = []
        for item in collection.list_indexes():
            result.append(
                {
                    "name": item.get("name"),
                    "keys": self._normalise_value(list(item.get("key", {}).items())),
                    "unique": bool(item.get("unique", False)),
                    "sparse": bool(item.get("sparse", False)),
                }
            )
        return result

    def schema_change_plan(self, operation: dict[str, Any]) -> dict[str, Any]:
        """Create a connector-specific dry-run schema change plan."""
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
        """Apply a previously planned schema change."""
        raw = dict(plan or {})
        op_type = str(raw.get("operation") or raw.get("op_type") or "").strip()
        namespace = str(raw.get("namespace") or "").strip()
        entity = str(raw.get("entity") or "").strip()
        parameters = dict(raw.get("parameters") or {})
        for key, value in raw.items():
            if key not in {"operation", "op_type", "namespace", "entity", "parameters"}:
                parameters[key] = value

        if op_type == "create_entity":
            database = self._client[namespace]
            options = dict(parameters.get("options") or {})
            database.create_collection(entity, **options)
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_created": True,
            }
        if op_type == "drop_entity":
            self._client[namespace][entity].drop()
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_dropped": True,
            }
        if op_type == "create_index":
            collection = self._client[namespace][entity]
            keys = self._coerce_index_keys(parameters.get("keys", []))
            name = collection.create_index(keys, unique=bool(parameters.get("unique", False)), name=parameters.get("name"))
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "index_name": name,
            }
        if op_type == "drop_index":
            collection = self._client[namespace][entity]
            collection.drop_index(parameters["name"])
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "index_name": parameters["name"],
            }
        raise ValueError(f"Unsupported schema change operation: {op_type}")

    def extract_relationships(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        """Infer relationships from sampled `_id` and `*_id` reference-like fields."""
        relationships: list[dict[str, Any]] = []
        for sample in self.sample_shapes(namespace, entity, n=25):
            for key, value in sample.items():
                if key == "_id":
                    continue
                if key.endswith("_id") or isinstance(value, ObjectId):
                    relationships.append(
                        {
                            "namespace": namespace,
                            "entity": entity,
                            "field": key,
                            "relationship_type": "reference_candidate",
                            "target_entity_hint": key[:-3] if key.endswith("_id") else None,
                        }
                    )
        deduped = {(item["entity"], item["field"]): item for item in relationships}
        return list(deduped.values())

    def close(self) -> None:
        """Close the MongoDB client."""
        self._client.close()

    def _plan_create_entity(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not namespace or not entity:
            raise ValueError("create_entity requires namespace and entity")
        exists = self._entity_exists(namespace, entity)
        if exists:
            raise ValueError(f"Collection already exists: {namespace}.{entity}")
        entities_before = self.list_entities(namespace)
        after_entities = entities_before + [{"name": entity, "type": "collection"}]
        return {
            "source_type": "mongodb",
            "operation": "create_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {
                "entity_exists": False,
                "entities": entities_before,
            },
            "after_state": {
                "entity_exists": True,
                "entities": after_entities,
            },
            "warnings": [],
        }

    def _plan_drop_entity(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not namespace or not entity:
            raise ValueError("drop_entity requires namespace and entity")
        if not self._entity_exists(namespace, entity):
            raise ValueError(f"Collection does not exist: {namespace}.{entity}")
        before_entities = self.list_entities(namespace)
        describe = self.describe_entity(namespace, entity)
        return {
            "source_type": "mongodb",
            "operation": "drop_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {
                "entity_exists": True,
                "entity": describe,
                "entities": before_entities,
            },
            "after_state": {
                "entity_exists": False,
                "entities": [item for item in before_entities if str(item.get("name")) != entity],
            },
            "warnings": ["Dropping a collection permanently removes its documents and indexes."],
        }

    def _plan_create_index(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not self._entity_exists(namespace, entity):
            raise ValueError(f"Collection does not exist: {namespace}.{entity}")
        keys = self._coerce_index_keys(parameters.get("keys", []))
        if not keys:
            raise ValueError("create_index requires at least one key")
        current_indexes = self.list_indexes(namespace, entity)
        requested_name = str(parameters.get("name") or self._default_index_name(parameters.get("keys", []))).strip()
        if any(str(item.get("name")) == requested_name for item in current_indexes):
            raise ValueError(f"Index already exists: {requested_name}")
        planned_index = {
            "name": requested_name,
            "keys": self._normalise_value(keys),
            "unique": bool(parameters.get("unique", False)),
            "sparse": bool(parameters.get("sparse", False)),
        }
        return {
            "source_type": "mongodb",
            "operation": "create_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": {**parameters, "name": requested_name, "keys": parameters.get("keys", [])},
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {"indexes": current_indexes},
            "after_state": {"indexes": current_indexes + [planned_index]},
            "warnings": [],
        }

    def _plan_drop_index(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not self._entity_exists(namespace, entity):
            raise ValueError(f"Collection does not exist: {namespace}.{entity}")
        index_name = str(parameters.get("name") or "").strip()
        if not index_name:
            raise ValueError("drop_index requires an index name")
        current_indexes = self.list_indexes(namespace, entity)
        if not any(str(item.get("name")) == index_name for item in current_indexes):
            raise ValueError(f"Index does not exist: {index_name}")
        return {
            "source_type": "mongodb",
            "operation": "drop_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": {"name": index_name},
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {"indexes": current_indexes},
            "after_state": {"indexes": [item for item in current_indexes if str(item.get("name")) != index_name]},
            "warnings": ["Dropping an index may increase query latency until a replacement is created."],
        }

    def _entity_exists(self, namespace: str, entity: str) -> bool:
        return any(str(item.get("name")) == entity for item in self.list_entities(namespace))

    @staticmethod
    def _default_index_name(keys: list[dict[str, Any]]) -> str:
        parts = []
        for item in keys:
            field = str(item.get("field", "")).strip()
            direction = str(item.get("direction", "asc")).strip().lower()
            parts.append(f"{field}_{direction}")
        return "_".join(parts) or "generated_index"

    @staticmethod
    def _coerce_index_keys(keys: list[dict[str, Any]]) -> list[tuple[str, int]]:
        result: list[tuple[str, int]] = []
        for item in keys:
            field = str(item.get("field", "")).strip()
            if not field:
                continue
            direction = ASCENDING if str(item.get("direction", "asc")).lower() == "asc" else DESCENDING
            result.append((field, direction))
        return result

    @staticmethod
    def _normalise_document(document: dict[str, Any]) -> dict[str, Any]:
        """Convert BSON values to JSON-safe values for API responses."""
        result: dict[str, Any] = {}
        for key, value in document.items():
            result[key] = MongoDBConnector._normalise_value(value)
        return result

    @staticmethod
    def _normalise_value(value: Any) -> Any:
        """Convert BSON values to JSON-safe values recursively."""
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, Binary):
            return bytes(value).hex()
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).hex()
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, dict):
            return {key: MongoDBConnector._normalise_value(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [MongoDBConnector._normalise_value(item) for item in value]
        if isinstance(value, list):
            return [MongoDBConnector._normalise_value(item) for item in value]
        return value

    @staticmethod
    def _field_type_name(value: Any) -> str:
        """Return a stable schema type label for MongoDB sample values."""
        if isinstance(value, (Binary, bytes, bytearray, memoryview)):
            return "binary"
        if isinstance(value, ObjectId):
            return "object_id"
        if isinstance(value, datetime):
            return "datetime"
        return type(value).__name__
