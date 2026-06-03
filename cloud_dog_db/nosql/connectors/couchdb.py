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
# Description: CouchDB adapter implementing the shared db-mcp-server connector contract.
# Related requirements: CN-01, CD-02, SC-01, CO-01, CO-02, RL-01
# Covers: CN-02
# Related tests: UT1.13, ST1.9, IT1.8

"""CouchDB connector adapter for db-mcp-server."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import requests


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CouchDBConnector:
    """CouchDB adapter implementing the connector contract."""

    def __init__(self, *, uri: str, timeout_seconds: int = 30) -> None:
        self._uri = uri
        self._timeout_seconds = timeout_seconds
        self._base_url, auth = self._normalise_uri(uri)
        self._session = requests.Session()
        if auth is not None:
            self._session.auth = auth
        self._session.headers.update({"Accept": "application/json"})
        self._verified_namespaces: set[str] = set()  # lazy auto-create cache

    @property
    def uri(self) -> str:
        """Return the configured CouchDB connection URI."""
        return self._uri

    def capability_report(self) -> dict[str, Any]:
        """Return the CouchDB adapter capability set."""
        return {
            "source_type": "couchdb",
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
        """Validate connectivity to the configured CouchDB deployment."""
        try:
            status = self._request_json("GET", "/_up")
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            status = self._request_json("GET", "/")
        return {"ok": True, "source_type": "couchdb", "status": status, "timestamp": _utcnow()}

    def list_namespaces(self) -> list[dict[str, Any]]:
        """List CouchDB databases excluding internal ones."""
        database_names = self._request_json("GET", "/_all_dbs")
        return [
            {"name": name, "type": "database"}
            for name in database_names
            if isinstance(name, str) and not name.startswith("_")
        ]

    def list_entities(self, namespace: str) -> list[dict[str, Any]]:
        """List logical document entities and user-facing view abstractions within a database."""
        documents = self._list_documents(namespace)
        design_docs = self._design_docs(namespace)
        entities: list[dict[str, Any]] = [{"name": "_documents", "type": "documents"}]

        doc_types = sorted(
            {
                str(item.get("doc_type"))
                for item in documents
                if str(item.get("doc_type", "")).strip()
            }
        )
        for doc_type in doc_types:
            entities.append({"name": doc_type, "type": "document_set"})

        for entity in sorted(self._managed_entities(design_docs)):
            if entity not in doc_types:
                entities.append({"name": entity, "type": "document_set"})

        for design_name, design_doc in sorted(design_docs.items()):
            if design_name.startswith("dbmcp_entity_"):
                continue
            for view_name in sorted((design_doc.get("views") or {}).keys()):
                entities.append({"name": f"{design_name}/{view_name}", "type": "view"})
        return entities

    def describe_entity(self, namespace: str, entity: str) -> dict[str, Any]:
        """Describe logical document or view metadata for a CouchDB entity."""
        kind = self._entity_kind(namespace, entity)
        if kind == "view":
            design_name, view_name = entity.split("/", 1)
            design_doc = self._design_docs(namespace)[design_name]
            view_payload = self._request_json(
                "GET",
                self._view_path(namespace, design_name, view_name),
                params={"limit": 0, "reduce": "false"},
            )
            return {
                "namespace": namespace,
                "entity": entity,
                "entity_type": "view",
                "document_count": int(view_payload.get("total_rows", 0)),
                "design_document": design_name,
                "view_name": view_name,
                "view_definition": self._normalise_value((design_doc.get("views") or {}).get(view_name, {})),
                "indexes": self.list_indexes(namespace, entity),
            }

        documents = self._entity_documents(namespace, entity)
        managed = self._managed_entity_definition(namespace, entity)
        return {
            "namespace": namespace,
            "entity": entity,
            "entity_type": "documents" if entity == "_documents" else "document_set",
            "document_count": len(documents),
            "managed_entity": managed,
            "indexes": self.list_indexes(namespace, entity),
        }

    def describe_fields(self, namespace: str, entity: str) -> dict[str, Any]:
        """Infer field types from sampled CouchDB documents."""
        samples = self.sample_shapes(namespace, entity, n=25)
        field_types: dict[str, set[str]] = defaultdict(set)
        for sample in samples:
            for key, value in sample.items():
                field_types[key].add(type(value).__name__)
        payload: dict[str, Any] = {
            "namespace": namespace,
            "entity": entity,
            "fields": [
                {"name": key, "types": sorted(values)}
                for key, values in sorted(field_types.items())
            ],
            "sample_count": len(samples),
        }
        if "/" in entity:
            design_name, view_name = entity.split("/", 1)
            payload["view_metadata"] = self._normalise_value(
                (self._design_docs(namespace).get(design_name, {}).get("views") or {}).get(view_name, {})
            )
        return payload

    def read(
        self,
        namespace: str,
        entity: str,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read CouchDB documents through the logical entity abstraction."""
        if self._entity_kind(namespace, entity) == "view":
            rows = self._view_documents(namespace, entity)
        else:
            rows = self._entity_documents(namespace, entity)
        matched = [item for item in rows if self._matches_filter(item, filter or {})]
        ordered = self._apply_sort(matched, sort or [])
        if limit is not None:
            ordered = ordered[: max(0, int(limit))]
        return [self._apply_projection(item, projection) for item in ordered]

    def _ensure_namespace_exists(self, namespace: str) -> None:
        """Lazy auto-create: ensure CouchDB database exists (called on first write)."""
        if namespace in self._verified_namespaces:
            return
        try:
            r = self._session.head(f"{self._base_url}/{namespace}", timeout=10)
            if r.status_code == 404:
                self._session.put(f"{self._base_url}/{namespace}", timeout=10).raise_for_status()
        except Exception:
            pass  # Proceed — the actual write will surface a clear error if DB is missing
        self._verified_namespaces.add(namespace)

    def create(self, namespace: str, entity: str, document: dict[str, Any]) -> dict[str, Any]:
        """Create a CouchDB content item within a logical entity."""
        self._ensure_namespace_exists(namespace)
        self._ensure_mutable_entity(namespace, entity)
        payload = self._prepare_write_document(entity, document)
        if "_id" in payload and str(payload["_id"]).strip():
            doc_id = str(payload["_id"])
            self._request_json("PUT", self._document_path(namespace, doc_id), json=payload)
        else:
            response = self._request_json("POST", self._database_path(namespace), json=payload)
            doc_id = str(response["id"])
        created = self._request_json("GET", self._document_path(namespace, doc_id))
        return {
            "inserted_id": doc_id,
            "document": self._normalise_document(created),
        }

    def update(self, namespace: str, entity: str, filter: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        """Update CouchDB documents matching the structured filter."""
        self._ensure_namespace_exists(namespace)
        self._ensure_mutable_entity(namespace, entity)
        matched = self._matching_documents(namespace, entity, filter or {})
        modified = 0
        for document in matched:
            changed = self._apply_update_document(document, update)
            if changed:
                self._request_json("PUT", self._document_path(namespace, str(document["_id"])), json=document)
                modified += 1
        return {
            "matched_count": len(matched),
            "modified_count": modified,
        }

    def delete(self, namespace: str, entity: str, filter: dict[str, Any]) -> dict[str, Any]:
        """Delete CouchDB documents matching the structured filter."""
        self._ensure_mutable_entity(namespace, entity)
        matched = self._matching_documents(namespace, entity, filter or {})
        for document in matched:
            self._request_json(
                "DELETE",
                self._document_path(namespace, str(document["_id"])),
                params={"rev": str(document["_rev"])},
            )
        return {"deleted_count": len(matched)}

    def count(self, namespace: str, entity: str, filter: dict[str, Any] | None = None) -> int:
        """Count CouchDB documents matching the structured filter."""
        return len(self._matching_documents(namespace, entity, filter or {}))

    def sample_shapes(self, namespace: str, entity: str, n: int = 10) -> list[dict[str, Any]]:
        """Sample CouchDB documents for shape and field inference."""
        if self._entity_kind(namespace, entity) == "view":
            rows = self._view_documents(namespace, entity)
        else:
            rows = self._entity_documents(namespace, entity)
        return [self._normalise_document(item) for item in rows[: max(1, int(n))]]

    def list_indexes(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        """Return Mango indexes and view metadata relevant to the entity."""
        indexes = self._request_json("GET", f"{self._database_path(namespace)}/_index").get("indexes", [])
        result: list[dict[str, Any]] = []
        for item in indexes:
            normalised = {
                "name": item.get("name"),
                "ddoc": item.get("ddoc"),
                "type": item.get("type", "json"),
                "fields": self._normalise_value((item.get("def") or {}).get("fields", [])),
                "partial_filter_selector": self._normalise_value((item.get("def") or {}).get("partial_filter_selector")),
            }
            if self._index_matches_entity(entity, normalised):
                result.append(normalised)

        if "/" in entity:
            design_name, view_name = entity.split("/", 1)
            view_definition = (self._design_docs(namespace).get(design_name, {}).get("views") or {}).get(view_name)
            if view_definition is not None:
                result.append(
                    {
                        "name": entity,
                        "type": "view",
                        "fields": [],
                        "definition": self._normalise_value(view_definition),
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
            design_doc = self._managed_entity_design_doc(entity, parameters)
            self._request_json(
                "PUT",
                self._document_path(namespace, str(design_doc["_id"])),
                json=design_doc,
            )
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_created": True,
            }

        if op_type == "drop_entity":
            design_doc = self._managed_entity_document(namespace, entity)
            if design_doc is None:
                raise ValueError(f"Managed entity design document not found: {entity}")
            self._request_json(
                "DELETE",
                self._document_path(namespace, str(design_doc["_id"])),
                params={"rev": str(design_doc["_rev"])} ,
            )
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_dropped": True,
            }

        if op_type == "create_index":
            self._ensure_not_view_entity(entity)
            body = {
                "index": {"fields": self._mango_index_fields(parameters.get("keys", []))},
                "name": parameters["name"],
                "type": "json",
            }
            partial = self._entity_partial_selector(entity)
            if partial is not None:
                body["index"]["partial_filter_selector"] = partial
            response = self._request_json("POST", f"{self._database_path(namespace)}/_index", json=body)
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "index_name": parameters["name"],
                "result": self._normalise_value(response),
            }

        if op_type == "drop_index":
            ddoc = str(parameters.get("ddoc") or "").strip()
            name = str(parameters.get("name") or "").strip()
            index_type = str(parameters.get("type") or "json").strip() or "json"
            if not ddoc or not name:
                raise ValueError("drop_index requires ddoc and name")
            self._request_json("DELETE", f"{self._database_path(namespace)}/_index/{quote(ddoc, safe='')}/{quote(index_type, safe='')}/{quote(name, safe='')}")
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "index_name": name,
            }

        raise ValueError(f"Unsupported schema change operation: {op_type}")

    def extract_relationships(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        """Infer relationship candidates from sampled documents."""
        relationships: dict[tuple[str, str], dict[str, Any]] = {}
        for sample in self.sample_shapes(namespace, entity, n=25):
            for key, value in sample.items():
                if key in {"_id", "_rev"}:
                    continue
                if key.endswith("_id") or self._is_reference_list(value):
                    relationships[(entity, key)] = {
                        "namespace": namespace,
                        "entity": entity,
                        "field": key,
                        "relationship_type": "reference_candidate",
                        "target_entity_hint": key[:-3] if key.endswith("_id") else None,
                    }
        return list(relationships.values())

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def _plan_create_entity(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not namespace or not entity:
            raise ValueError("create_entity requires namespace and entity")
        if "/" in entity or entity == "_documents":
            raise ValueError(f"Unsupported CouchDB entity name: {entity}")
        if self._has_entity(namespace, entity):
            raise ValueError(f"Entity already exists: {namespace}.{entity}")
        before_entities = self.list_entities(namespace)
        return {
            "source_type": "couchdb",
            "operation": "create_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {
                "entity_exists": False,
                "entities": before_entities,
            },
            "after_state": {
                "entity_exists": True,
                "entities": before_entities + [{"name": entity, "type": "document_set"}],
            },
            "warnings": ["CouchDB entities are logical document sets backed by managed design documents."],
        }

    def _plan_drop_entity(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not namespace or not entity:
            raise ValueError("drop_entity requires namespace and entity")
        if not self._has_entity(namespace, entity):
            raise ValueError(f"Entity does not exist: {namespace}.{entity}")
        before_entities = self.list_entities(namespace)
        return {
            "source_type": "couchdb",
            "operation": "drop_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {
                "entity_exists": True,
                "entity": self.describe_entity(namespace, entity),
                "entities": before_entities,
            },
            "after_state": {
                "entity_exists": False,
                "entities": [item for item in before_entities if str(item.get("name")) != entity],
            },
            "warnings": ["Dropping a CouchDB entity removes its managed design document but does not delete matching content items."],
        }

    def _plan_create_index(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self._ensure_not_view_entity(entity)
        keys = list(parameters.get("keys", []))
        if not keys:
            raise ValueError("create_index requires at least one key")
        current_indexes = self.list_indexes(namespace, entity)
        requested_name = str(parameters.get("name") or self._default_index_name(keys)).strip()
        if any(str(item.get("name")) == requested_name for item in current_indexes):
            raise ValueError(f"Index already exists: {requested_name}")
        planned = {
            "name": requested_name,
            "type": "json",
            "fields": self._normalise_value(self._mango_index_fields(keys)),
            "partial_filter_selector": self._entity_partial_selector(entity),
        }
        return {
            "source_type": "couchdb",
            "operation": "create_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": {**parameters, "name": requested_name, "keys": keys},
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {"indexes": current_indexes},
            "after_state": {"indexes": current_indexes + [planned]},
            "warnings": [],
        }

    def _plan_drop_index(self, namespace: str, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self._ensure_not_view_entity(entity)
        index_name = str(parameters.get("name") or "").strip()
        if not index_name:
            raise ValueError("drop_index requires an index name")
        current_indexes = self.list_indexes(namespace, entity)
        existing = next((item for item in current_indexes if str(item.get("name")) == index_name and str(item.get("type")) != "view"), None)
        if existing is None:
            raise ValueError(f"Index does not exist: {index_name}")
        return {
            "source_type": "couchdb",
            "operation": "drop_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": {
                "name": index_name,
                "ddoc": existing.get("ddoc"),
                "type": existing.get("type", "json"),
            },
            "dry_run": True,
            "requires_approval": True,
            "planned_at": _utcnow(),
            "before_state": {"indexes": current_indexes},
            "after_state": {"indexes": [item for item in current_indexes if str(item.get("name")) != index_name]},
            "warnings": ["Dropping a Mango index may increase selector latency until a replacement is created."],
        }

    def _has_entity(self, namespace: str, entity: str) -> bool:
        return any(str(item.get("name")) == entity for item in self.list_entities(namespace))

    def _entity_kind(self, namespace: str, entity: str) -> str:
        if entity == "_documents":
            return "documents"
        if "/" in entity:
            return "view"
        managed = self._managed_entity_document(namespace, entity)
        if managed is not None:
            return "document_set"
        return "document_set"

    def _entity_documents(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        documents = self._list_documents(namespace)
        if entity == "_documents":
            return documents
        return [item for item in documents if str(item.get("doc_type", "")).strip() == entity]

    def _view_documents(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        design_name, view_name = entity.split("/", 1)
        payload = self._request_json(
            "GET",
            self._view_path(namespace, design_name, view_name),
            params={"include_docs": "true", "reduce": "false"},
        )
        rows = []
        for row in payload.get("rows", []):
            document = row.get("doc")
            if isinstance(document, dict):
                rows.append(self._normalise_document(document))
        return rows

    def _matching_documents(self, namespace: str, entity: str, filter_spec: dict[str, Any]) -> list[dict[str, Any]]:
        source = self._view_documents(namespace, entity) if self._entity_kind(namespace, entity) == "view" else self._entity_documents(namespace, entity)
        return [item for item in source if self._matches_filter(item, filter_spec)]

    def _list_documents(self, namespace: str) -> list[dict[str, Any]]:
        payload = self._request_json(
            "GET",
            f"{self._database_path(namespace)}/_all_docs",
            params={"include_docs": "true"},
        )
        documents = []
        for row in payload.get("rows", []):
            document = row.get("doc")
            if not isinstance(document, dict):
                continue
            if str(document.get("_id", "")).startswith("_design/"):
                continue
            documents.append(self._normalise_document(document))
        return documents

    def _design_docs(self, namespace: str) -> dict[str, dict[str, Any]]:
        payload = self._request_json(
            "GET",
            f"{self._database_path(namespace)}/_all_docs",
            params={"include_docs": "true", "startkey": '"_design/"', "endkey": '"_design0"'},
        )
        design_docs: dict[str, dict[str, Any]] = {}
        for row in payload.get("rows", []):
            document = row.get("doc")
            if not isinstance(document, dict):
                continue
            doc_id = str(document.get("_id", ""))
            if not doc_id.startswith("_design/"):
                continue
            design_docs[doc_id.removeprefix("_design/")] = document
        return design_docs

    @staticmethod
    def _managed_entities(design_docs: dict[str, dict[str, Any]]) -> list[str]:
        result = []
        for name in design_docs:
            if name.startswith("dbmcp_entity_"):
                result.append(name.removeprefix("dbmcp_entity_"))
        return result

    def _managed_entity_definition(self, namespace: str, entity: str) -> dict[str, Any] | None:
        design = self._managed_entity_document(namespace, entity)
        if design is None:
            return None
        return {
            "design_document": str(design.get("_id")),
            "views": self._normalise_value(design.get("views", {})),
        }

    def _managed_entity_document(self, namespace: str, entity: str) -> dict[str, Any] | None:
        design_name = self._managed_entity_design_name(entity)
        return self._design_docs(namespace).get(design_name)

    @staticmethod
    def _managed_entity_design_name(entity: str) -> str:
        return f"dbmcp_entity_{entity}"

    def _managed_entity_design_doc(self, entity: str, parameters: dict[str, Any]) -> dict[str, Any]:
        field_name = str(parameters.get("field") or "doc_type").strip() or "doc_type"
        match_value = parameters.get("match_value", entity)
        escaped_value = str(match_value).replace("\\", "\\\\").replace('"', '\\"')
        escaped_field = field_name.replace("\\", "\\\\").replace('"', '\\"')
        return {
            "_id": f"_design/{self._managed_entity_design_name(entity)}",
            "language": "javascript",
            "views": {
                "items": {
                    "map": f'function(doc){{ if(doc["{escaped_field}"] === "{escaped_value}"){{ emit(doc._id, null); }} }}',
                }
            },
        }

    @staticmethod
    def _ensure_not_view_entity(entity: str) -> None:
        if "/" in entity:
            raise ValueError(f"Schema change operations are not supported for view entities: {entity}")

    def _ensure_mutable_entity(self, namespace: str, entity: str) -> None:
        self._ensure_not_view_entity(entity)
        if entity != "_documents" and not self._has_entity(namespace, entity):
            raise ValueError(f"Entity does not exist: {namespace}.{entity}")

    @staticmethod
    def _prepare_write_document(entity: str, document: dict[str, Any]) -> dict[str, Any]:
        payload = {str(key): value for key, value in dict(document or {}).items()}
        if entity != "_documents" and not str(payload.get("doc_type", "")).strip():
            payload["doc_type"] = entity
        return payload

    @staticmethod
    def _apply_update_document(document: dict[str, Any], update: dict[str, Any]) -> bool:
        changed = False
        operators = [key for key in update.keys() if str(key).startswith("$")]
        if not operators:
            for key, value in update.items():
                if document.get(key) != value:
                    document[key] = value
                    changed = True
            return changed
        if "$set" in update:
            for key, value in dict(update.get("$set") or {}).items():
                if document.get(key) != value:
                    document[key] = value
                    changed = True
        if "$unset" in update:
            for key in dict(update.get("$unset") or {}).keys():
                if key in document:
                    document.pop(key, None)
                    changed = True
        if "$inc" in update:
            for key, value in dict(update.get("$inc") or {}).items():
                current = document.get(key, 0)
                document[key] = current + value
                changed = True
        return changed

    @staticmethod
    def _is_reference_list(value: Any) -> bool:
        return isinstance(value, list) and value and all(isinstance(item, str) for item in value)

    @staticmethod
    def _apply_sort(rows: list[dict[str, Any]], sort_spec: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = list(rows)
        for item in reversed(sort_spec):
            field = str(item.get("field", "")).strip()
            if not field:
                continue
            reverse = str(item.get("direction", "asc")).strip().lower() == "desc"
            ordered.sort(key=lambda row: CouchDBConnector._sort_key(row.get(field)), reverse=reverse)
        return ordered

    @staticmethod
    def _sort_key(value: Any) -> tuple[int, Any]:
        if value is None:
            return (1, "")
        if isinstance(value, (int, float, str)):
            return (0, value)
        return (0, str(value))

    @staticmethod
    def _apply_projection(document: dict[str, Any], projection: dict[str, Any] | None) -> dict[str, Any]:
        if not projection:
            return dict(document)
        include = [key for key, enabled in projection.items() if bool(enabled)]
        exclude = [key for key, enabled in projection.items() if not bool(enabled)]
        if include:
            result = {key: document[key] for key in include if key in document}
            if projection.get("_id", 1) and "_id" in document and "_id" not in result:
                result["_id"] = document["_id"]
            return result
        result = dict(document)
        for key in exclude:
            result.pop(key, None)
        return result

    @staticmethod
    def _matches_filter(document: dict[str, Any], filter_spec: dict[str, Any]) -> bool:
        if not filter_spec:
            return True
        for key, expected in filter_spec.items():
            if key == "$and":
                return all(CouchDBConnector._matches_filter(document, item) for item in list(expected or []))
            if key == "$or":
                return any(CouchDBConnector._matches_filter(document, item) for item in list(expected or []))
            if key == "$nor":
                return not any(CouchDBConnector._matches_filter(document, item) for item in list(expected or []))

            exists = key in document
            actual = document.get(key)
            if isinstance(expected, dict):
                for operator, value in expected.items():
                    if operator == "$ne" and actual == value:
                        return False
                    if operator == "$gt" and not (actual is not None and actual > value):
                        return False
                    if operator == "$gte" and not (actual is not None and actual >= value):
                        return False
                    if operator == "$lt" and not (actual is not None and actual < value):
                        return False
                    if operator == "$lte" and not (actual is not None and actual <= value):
                        return False
                    if operator == "$in" and actual not in list(value or []):
                        return False
                    if operator == "$nin" and actual in list(value or []):
                        return False
                    if operator == "$regex":
                        pattern = str(value or "")
                        if actual is None or re.search(pattern, str(actual)) is None:
                            return False
                    if operator == "$exists" and bool(value) != exists:
                        return False
                continue
            if actual != expected:
                return False
        return True

    @staticmethod
    def _normalise_document(document: dict[str, Any]) -> dict[str, Any]:
        return {str(key): CouchDBConnector._normalise_value(value) for key, value in document.items()}

    @staticmethod
    def _normalise_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): CouchDBConnector._normalise_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [CouchDBConnector._normalise_value(item) for item in value]
        if isinstance(value, tuple):
            return [CouchDBConnector._normalise_value(item) for item in value]
        return value

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._session.request(
            method,
            f"{self._base_url}{path}",
            timeout=self._timeout_seconds,
            **kwargs,
        )
        response.raise_for_status()
        if not response.text:
            return {}
        return response.json()

    def _database_path(self, namespace: str) -> str:
        return f"/{quote(namespace, safe='')}"

    def _document_path(self, namespace: str, document_id: str) -> str:
        return f"{self._database_path(namespace)}/{quote(document_id, safe='')}"

    def _view_path(self, namespace: str, design_name: str, view_name: str) -> str:
        return f"{self._database_path(namespace)}/_design/{quote(design_name, safe='')}/_view/{quote(view_name, safe='')}"

    @staticmethod
    def _default_index_name(keys: list[dict[str, Any]]) -> str:
        parts = []
        for item in keys:
            field = str(item.get("field", "")).strip()
            direction = str(item.get("direction", "asc")).strip().lower()
            parts.append(f"{field}_{direction}")
        return "_".join(parts) or f"idx_{uuid4().hex[:8]}"

    @staticmethod
    def _mango_index_fields(keys: list[dict[str, Any]]) -> list[dict[str, str]]:
        fields: list[dict[str, str]] = []
        for item in keys:
            field = str(item.get("field", "")).strip()
            if not field:
                continue
            direction = str(item.get("direction", "asc")).strip().lower() or "asc"
            fields.append({field: direction})
        return fields

    @staticmethod
    def _entity_partial_selector(entity: str) -> dict[str, Any] | None:
        if entity == "_documents":
            return None
        return {"doc_type": entity}

    @staticmethod
    def _index_matches_entity(entity: str, index_payload: dict[str, Any]) -> bool:
        partial = index_payload.get("partial_filter_selector") or {}
        if entity == "_documents":
            return partial in ({}, None)
        if not partial:
            return False
        return partial in ({"doc_type": entity}, {"doc_type": {"$eq": entity}})

    @staticmethod
    def _normalise_uri(uri: str) -> tuple[str, tuple[str, str] | None]:
        parts = urlsplit(uri)
        netloc = parts.netloc
        auth: tuple[str, str] | None = None
        if parts.username is not None or parts.password is not None:
            host = parts.hostname or ""
            if parts.port is not None:
                host = f"{host}:{parts.port}"
            netloc = host
            auth = (parts.username or "", parts.password or "")
        base_url = urlunsplit((parts.scheme or "http", netloc, parts.path.rstrip("/"), "", ""))
        return base_url.rstrip("/"), auth
