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
# Description: Elasticsearch adapter implementing the db-mcp-server connector contract.
# Related requirements: CN-01, CD-02, SC-01, CO-01, CO-02, RL-01
# Covers: CN-04
# Related tests: UT1.16, ST1.12, IT1.11
# Recent changes:
#   W28A-274-F — Initial implementation

"""Elasticsearch connector adapter for db-mcp-server."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import fnmatch
from typing import Any
from urllib.parse import urlparse

from elasticsearch import Elasticsearch
from elasticsearch import NotFoundError


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ElasticsearchConnector:
    """Elasticsearch adapter implementing the connector contract.

    Uses the ``elasticsearch-py`` client (>=8.0) which diverges from
    ``opensearch-py`` in several API details — authentication helpers,
    index-template endpoints, and keyword-arg signatures.

    Args:
        uri: Full Elasticsearch endpoint URI including scheme, host, and port.
            Legacy URI user information is passed separately to the client as
            ``basic_auth`` and is never embedded in a host URL.
        timeout_seconds: Per-request timeout passed to the transport layer.

    Related tests:
        UT1.16, ST1.12, IT1.11
    """

    def __init__(self, *, uri: str, timeout_seconds: int = 30) -> None:
        self._uri = uri
        self._timeout_seconds = timeout_seconds
        self._client = self._build_client(uri, timeout_seconds)
        self._cluster_name: str | None = None

    @staticmethod
    def _build_client(uri: str, timeout_seconds: int) -> Elasticsearch:
        """Construct an Elasticsearch client from a URI string.

        Args:
            uri: Connection endpoint URI (``scheme://host[:port]``), optionally
                carrying legacy user information for separate client auth.
            timeout_seconds: Request timeout.

        Returns:
            Configured Elasticsearch client instance.
        """
        parsed = urlparse(uri)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError("Elasticsearch URI must include scheme and host")
        host = f"{parsed.scheme}://{parsed.hostname}"
        port = parsed.port or (443 if parsed.scheme == "https" else 9200)
        host = f"{host}:{port}"
        kwargs: dict[str, Any] = {
            "hosts": [host],
            "request_timeout": timeout_seconds,
        }
        if parsed.username:
            kwargs["basic_auth"] = (parsed.username, parsed.password or "")
        if parsed.scheme == "https":
            kwargs["verify_certs"] = False
            kwargs["ssl_show_warn"] = False
        return Elasticsearch(**kwargs)

    @property
    def uri(self) -> str:
        """Return the configured Elasticsearch URI."""
        return self._uri

    def capability_report(self) -> dict[str, Any]:
        """Return the Elasticsearch adapter capability set.

        Returns:
            Dictionary describing which connector operations are supported.
        """
        return {
            "source_type": "elasticsearch",
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
                "content_search": True,
            },
            "schema_change_operations": [
                "create_entity",
                "drop_entity",
                "create_index",
                "drop_index",
            ],
            "timestamp": _utcnow(),
        }

    def validate_profile(self) -> dict[str, Any]:
        """Validate connectivity to the configured Elasticsearch deployment.

        Returns:
            Validation result with cluster name and timestamp.

        Raises:
            RuntimeError: If the cluster does not respond to a ping.
        """
        if not self._client.ping():
            raise RuntimeError("Elasticsearch cluster did not respond to ping")
        return {
            "ok": True,
            "source_type": "elasticsearch",
            "cluster_name": self._namespace(),
            "timestamp": _utcnow(),
        }

    def list_namespaces(self) -> list[dict[str, Any]]:
        """List the single Elasticsearch cluster namespace.

        Returns:
            List containing the cluster namespace entry.
        """
        return [{"name": self._namespace(), "type": "cluster"}]

    def list_entities(self, namespace: str) -> list[dict[str, Any]]:
        """List indices and aliases within the cluster namespace.

        Args:
            namespace: Cluster namespace (must match the cluster name).

        Returns:
            List of entity descriptors (index or alias).
        """
        self._ensure_namespace(namespace)
        items: list[dict[str, Any]] = []
        for name in sorted(self._list_indices()):
            items.append({"name": name, "type": "index"})
        for alias, targets in sorted(self._alias_map().items()):
            items.append({"name": alias, "type": "alias", "targets": sorted(targets)})
        return items

    def describe_entity(self, namespace: str, entity: str) -> dict[str, Any]:
        """Describe index or alias metadata using Elasticsearch mappings and counts.

        Args:
            namespace: Cluster namespace.
            entity: Index or alias name.

        Returns:
            Entity metadata including document count and aliases.
        """
        self._ensure_namespace(namespace)
        entity_type = self._entity_type(entity)
        targets = self._resolve_targets(entity)
        return {
            "namespace": namespace,
            "entity": entity,
            "entity_type": entity_type,
            "indices": targets,
            "document_count": self.count(namespace, entity, {}),
            "aliases": self._aliases_for_entity(entity),
            "indexes": self.list_indexes(namespace, entity),
        }

    def describe_fields(self, namespace: str, entity: str) -> dict[str, Any]:
        """Return mapping-defined field schema for an index or alias.

        Args:
            namespace: Cluster namespace.
            entity: Index or alias name.

        Returns:
            Field descriptors derived from the index mapping.
        """
        self._ensure_namespace(namespace)
        properties = self._merged_properties(entity)
        fields = []
        for name, definition in sorted(properties.items()):
            field: dict[str, Any] = {
                "name": name,
                "types": sorted(self._extract_types(definition)),
            }
            if "format" in definition:
                field["format"] = definition["format"]
            fields.append(field)
        return {
            "namespace": namespace,
            "entity": entity,
            "fields": fields,
            "sample_count": 0,
            "source": "mapping",
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
        """Read documents from an index or alias using Elasticsearch query DSL.

        Args:
            namespace: Cluster namespace.
            entity: Index or alias name.
            filter: Optional query DSL filter clause.
            projection: Optional field inclusion/exclusion map.
            sort: Optional list of sort directives.
            limit: Maximum number of documents to return.

        Returns:
            List of normalised document dicts.
        """
        self._ensure_namespace(namespace)
        response = self._client.search(
            index=entity,
            query=filter or {"match_all": {}},
            source=self._projection_to_source(projection),
            sort=self._sort_to_es(sort) if sort else None,
            size=int(limit or 50),
        )
        return [
            self._normalise_hit(hit)
            for hit in response.get("hits", {}).get("hits", [])
        ]

    def create(self, namespace: str, entity: str, document: dict[str, Any]) -> dict[str, Any]:
        """Index a document into an Elasticsearch entity.

        Args:
            namespace: Cluster namespace.
            entity: Target index name.
            document: Document body (may contain ``_id`` or ``id`` key).

        Returns:
            Result containing the inserted ID and the stored document.
        """
        self._ensure_namespace(namespace)
        payload = dict(document or {})
        doc_id = str(payload.pop("_id", payload.pop("id", "")) or "")
        kwargs: dict[str, Any] = {"index": entity, "document": payload, "refresh": True}
        if doc_id:
            kwargs["id"] = doc_id
        response = self._client.index(**kwargs)
        created = self._client.get(index=entity, id=response["_id"])
        return {
            "inserted_id": response["_id"],
            "document": self._normalise_hit(created),
        }

    def update(
        self,
        namespace: str,
        entity: str,
        filter: dict[str, Any],
        update: dict[str, Any],
    ) -> dict[str, Any]:
        """Update documents matching a structured filter using ``update_by_query``.

        Args:
            namespace: Cluster namespace.
            entity: Target index name.
            filter: Query DSL filter to match documents.
            update: Update specification with ``$set`` and/or ``$unset`` keys.

        Returns:
            Counts of matched and modified documents.
        """
        self._ensure_namespace(namespace)
        script = self._build_update_script(update)
        response = self._client.update_by_query(
            index=entity,
            query=filter or {"match_all": {}},
            script=script,
            refresh=True,
            conflicts="proceed",
        )
        return {
            "matched_count": int(response.get("total", 0)),
            "modified_count": int(response.get("updated", 0)),
        }

    def delete(self, namespace: str, entity: str, filter: dict[str, Any]) -> dict[str, Any]:
        """Delete documents matching a structured filter.

        Args:
            namespace: Cluster namespace.
            entity: Target index name.
            filter: Query DSL filter to match documents.

        Returns:
            Count of deleted documents.
        """
        self._ensure_namespace(namespace)
        response = self._client.delete_by_query(
            index=entity,
            query=filter or {"match_all": {}},
            refresh=True,
            conflicts="proceed",
        )
        return {"deleted_count": int(response.get("deleted", 0))}

    def count(self, namespace: str, entity: str, filter: dict[str, Any] | None = None) -> int:
        """Count documents matching a structured filter.

        Args:
            namespace: Cluster namespace.
            entity: Index or alias name.
            filter: Optional query DSL filter clause.

        Returns:
            Number of matching documents.
        """
        self._ensure_namespace(namespace)
        response = self._client.count(
            index=entity,
            query=filter or {"match_all": {}},
        )
        return int(response.get("count", 0))

    def sample_shapes(self, namespace: str, entity: str, n: int = 10) -> list[dict[str, Any]]:
        """Sample document shapes by reading a limited number of hits.

        Args:
            namespace: Cluster namespace.
            entity: Index or alias name.
            n: Maximum number of samples.

        Returns:
            List of normalised document dicts.
        """
        self._ensure_namespace(namespace)
        response = self._client.search(
            index=entity,
            query={"match_all": {}},
            size=int(max(1, n)),
        )
        return [
            self._normalise_hit(hit)
            for hit in response.get("hits", {}).get("hits", [])
        ]

    def list_indexes(self, namespace: str, entity: str) -> list[dict[str, Any]]:
        """List alias and template metadata relevant to an entity.

        Args:
            namespace: Cluster namespace.
            entity: Index or alias name.

        Returns:
            List of alias and index-template descriptors.
        """
        self._ensure_namespace(namespace)
        items: list[dict[str, Any]] = []
        if self._entity_type(entity) == "alias":
            items.append(
                {
                    "name": entity,
                    "type": "alias",
                    "targets": self._resolve_targets(entity),
                }
            )
        for alias in self._aliases_for_entity(entity):
            if alias != entity:
                items.append(
                    {
                        "name": alias,
                        "type": "alias",
                        "targets": self._resolve_targets(alias),
                    }
                )
        for template in self._matching_templates(entity):
            items.append(template)
        return items

    def schema_change_plan(self, operation: dict[str, Any]) -> dict[str, Any]:
        """Create a connector-specific dry-run schema change plan.

        Args:
            operation: Schema change specification including ``operation``,
                ``namespace``, ``entity``, and ``parameters``.

        Returns:
            Dry-run plan describing before/after state.

        Raises:
            ValueError: For unsupported operation types.
        """
        raw = dict(operation or {})
        op_type = str(raw.get("operation") or raw.get("op_type") or "").strip()
        namespace = str(raw.get("namespace") or self._namespace()).strip()
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
            plan: Plan dictionary (same shape as ``schema_change_plan`` output).

        Returns:
            Result indicating success/failure of the applied change.

        Raises:
            ValueError: For unsupported operation types.
        """
        raw = dict(plan or {})
        op_type = str(raw.get("operation") or raw.get("op_type") or "").strip()
        namespace = str(raw.get("namespace") or self._namespace()).strip()
        entity = str(raw.get("entity") or "").strip()
        parameters = dict(raw.get("parameters") or {})
        for key, value in raw.items():
            if key not in {"operation", "op_type", "namespace", "entity", "parameters"}:
                parameters[key] = value
        self._ensure_namespace(namespace)

        if op_type == "create_entity":
            body: dict[str, Any] = {}
            settings = dict(parameters.get("settings") or {})
            if settings:
                body["settings"] = settings
            properties = dict(parameters.get("properties") or {})
            mappings = dict(parameters.get("mappings") or {})
            if properties:
                body.setdefault("mappings", {})["properties"] = properties
            if mappings:
                body["mappings"] = mappings
            aliases = dict(parameters.get("aliases") or {})
            alias_name = str(parameters.get("alias") or "").strip()
            if alias_name:
                aliases.setdefault(alias_name, {})
            if aliases:
                body["aliases"] = aliases
            self._client.indices.create(index=entity, body=body or None)
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_created": True,
            }
        if op_type == "drop_entity":
            if self._entity_type(entity) == "alias":
                targets = self._resolve_targets(entity)
                for target in targets:
                    self._client.indices.delete_alias(index=target, name=entity)
                if bool(parameters.get("drop_backing_indices", False)):
                    for target in targets:
                        self._client.indices.delete(index=target)
            else:
                self._client.indices.delete(index=entity)
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "entity_dropped": True,
            }
        if op_type == "create_index":
            name = self._template_name(entity, parameters)
            self._client.indices.put_index_template(
                name=name,
                index_patterns=self._template_patterns(entity, parameters),
                template={
                    "mappings": {
                        "properties": self._template_properties(parameters),
                    },
                    "settings": dict(parameters.get("settings") or {}),
                },
            )
            return {
                "applied": True,
                "success": True,
                "operation": op_type,
                "namespace": namespace,
                "entity": entity,
                "index_name": name,
            }
        if op_type == "drop_index":
            name = str(
                parameters.get("name") or self._template_name(entity, parameters)
            ).strip()
            self._client.indices.delete_index_template(name=name)
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
        """Infer relationship candidates from mapping-defined identifier fields.

        Args:
            namespace: Cluster namespace.
            entity: Index or alias name.

        Returns:
            List of inferred relationship descriptors.
        """
        self._ensure_namespace(namespace)
        relationships: list[dict[str, Any]] = []
        for field, definition in sorted(self._merged_properties(entity).items()):
            field_types = self._extract_types(definition)
            if field.endswith("_id") and "keyword" in field_types:
                relationships.append(
                    {
                        "field": field,
                        "relationship_type": "reference_candidate",
                        "target_entity_hint": f"{field[:-3]}s",
                    }
                )
        return relationships

    def close(self) -> None:
        """Close the underlying Elasticsearch transport."""
        self._client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _namespace(self) -> str:
        if self._cluster_name is None:
            self._cluster_name = str(
                self._client.info().get("cluster_name", "elasticsearch")
            )
        return self._cluster_name

    def _ensure_namespace(self, namespace: str) -> None:
        if namespace and namespace != self._namespace():
            raise ValueError(f"Unknown Elasticsearch namespace: {namespace}")

    def _list_indices(self) -> list[str]:
        try:
            result = self._client.indices.get(index="*")
        except NotFoundError:
            return []
        return [name for name in sorted(result) if not name.startswith(".")]

    def _alias_map(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        try:
            aliases = self._client.indices.get_alias(index="*")
        except NotFoundError:
            return {}
        for index_name, payload in aliases.items():
            if index_name.startswith("."):
                continue
            for alias_name in payload.get("aliases", {}):
                result[alias_name].append(index_name)
        return dict(result)

    def _aliases_for_entity(self, entity: str) -> list[str]:
        aliases = []
        for alias, targets in self._alias_map().items():
            if entity == alias or entity in targets:
                aliases.append(alias)
        return sorted(aliases)

    def _entity_type(self, entity: str) -> str:
        alias_map = self._alias_map()
        if entity in alias_map:
            return "alias"
        if entity in self._list_indices():
            return "index"
        raise ValueError(f"Unknown Elasticsearch entity: {entity}")

    def _resolve_targets(self, entity: str) -> list[str]:
        alias_map = self._alias_map()
        if entity in alias_map:
            return sorted(alias_map[entity])
        if entity in self._list_indices():
            return [entity]
        raise ValueError(f"Unknown Elasticsearch entity: {entity}")

    def _merged_properties(self, entity: str) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for target in self._resolve_targets(entity):
            mappings = self._client.indices.get_mapping(index=target)
            index_mapping = mappings.get(target, {}).get("mappings", {})
            properties.update(dict(index_mapping.get("properties") or {}))
        return properties

    @staticmethod
    def _extract_types(definition: dict[str, Any]) -> set[str]:
        types = {str(definition.get("type", "object"))}
        for field_def in dict(definition.get("fields") or {}).values():
            field_type = str(field_def.get("type", "")).strip()
            if field_type:
                types.add(field_type)
        return types

    @staticmethod
    def _projection_to_source(
        projection: dict[str, Any] | None,
    ) -> bool | dict[str, Any]:
        if not projection:
            return True
        include = [key for key, value in projection.items() if int(value or 0) > 0]
        exclude = [key for key, value in projection.items() if int(value or 0) <= 0]
        if include and not exclude:
            return include
        return {"includes": include, "excludes": exclude}

    @staticmethod
    def _sort_to_es(sort: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                str(item.get("field", "")): {
                    "order": (
                        "asc"
                        if str(item.get("direction", "asc")).lower() == "asc"
                        else "desc"
                    )
                }
            }
            for item in sort
            if str(item.get("field", "")).strip()
        ]

    @staticmethod
    def _normalise_hit(hit: dict[str, Any]) -> dict[str, Any]:
        source = dict(hit.get("_source") or {})
        source["_id"] = str(hit.get("_id", source.get("_id", "")))
        if "_index" in hit:
            source["_index"] = str(hit["_index"])
        return source

    @staticmethod
    def _build_update_script(update: dict[str, Any]) -> dict[str, Any]:
        payload = dict(update or {})
        # Auto-wrap plain dicts in $set if no $ operators present
        if payload and not any(k.startswith("$") for k in payload):
            payload = {"$set": payload}
        set_values = dict(payload.get("$set") or {})
        unset_fields = payload.get("$unset") or []
        if isinstance(unset_fields, dict):
            unset_names = list(unset_fields)
        else:
            unset_names = list(unset_fields)
        statements = []
        if set_values:
            statements.append(
                "for (entry in params.set_values.entrySet()) "
                "{ ctx._source[entry.getKey()] = entry.getValue(); }"
            )
        if unset_names:
            statements.append(
                "for (field in params.unset_fields) "
                "{ ctx._source.remove(field); }"
            )
        if not statements:
            raise ValueError("Elasticsearch update requires $set and/or $unset")
        return {
            "lang": "painless",
            "source": " ".join(statements),
            "params": {"set_values": set_values, "unset_fields": unset_names},
        }

    def _matching_templates(self, entity: str) -> list[dict[str, Any]]:
        try:
            response = self._client.indices.get_index_template(name="*")
        except NotFoundError:
            return []
        results: list[dict[str, Any]] = []
        for item in response.get("index_templates", []):
            template = item.get("index_template", {})
            patterns = list(template.get("index_patterns") or [])
            if not any(fnmatch.fnmatch(entity, pattern) for pattern in patterns):
                continue
            properties = (
                template.get("template", {})
                .get("mappings", {})
                .get("properties", {})
            )
            results.append(
                {
                    "name": item.get("name"),
                    "type": "index_template",
                    "index_patterns": patterns,
                    "fields": sorted(properties),
                }
            )
        return sorted(results, key=lambda item: str(item.get("name", "")))

    def _plan_create_entity(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        exists = entity in self._list_indices() or entity in self._alias_map()
        return {
            "dry_run": True,
            "operation": "create_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "before_state": {"entity_exists": exists},
            "after_state": {
                "entity_exists": True,
                "aliases": sorted(
                    (dict(parameters.get("aliases") or {})).keys()
                    | ({str(parameters.get("alias", "")).strip()} - {""})
                ),
            },
        }

    def _plan_drop_entity(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        entity_type = self._entity_type(entity)
        return {
            "dry_run": True,
            "operation": "drop_entity",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "before_state": {
                "entity_exists": True,
                "entity_type": entity_type,
                "indices": self._resolve_targets(entity),
            },
            "after_state": {"entity_exists": False},
        }

    def _plan_create_index(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        name = self._template_name(entity, parameters)
        before = [item["name"] for item in self._matching_templates(entity)]
        after = sorted(set(before + [name]))
        return {
            "dry_run": True,
            "operation": "create_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": parameters,
            "before_state": {"indexes": before},
            "after_state": {"indexes": [{"name": item} for item in after]},
        }

    def _plan_drop_index(
        self, namespace: str, entity: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self._ensure_namespace(namespace)
        name = str(
            parameters.get("name") or self._template_name(entity, parameters)
        ).strip()
        before = [item["name"] for item in self._matching_templates(entity)]
        after = [item for item in before if item != name]
        return {
            "dry_run": True,
            "operation": "drop_index",
            "namespace": namespace,
            "entity": entity,
            "parameters": {"name": name, **parameters},
            "before_state": {"indexes": before},
            "after_state": {"indexes": after},
        }

    @staticmethod
    def _template_name(entity: str, parameters: dict[str, Any]) -> str:
        explicit = str(parameters.get("name") or "").strip()
        if explicit:
            return explicit
        keys = [
            f"{str(item.get('field', '')).strip()}_{str(item.get('direction', 'asc')).lower()}"
            for item in list(parameters.get("keys") or [])
            if str(item.get("field", "")).strip()
        ]
        suffix = "__".join(keys) if keys else "template"
        return f"dbmcp_{entity}_{suffix}"

    @staticmethod
    def _template_patterns(entity: str, parameters: dict[str, Any]) -> list[str]:
        patterns = list(parameters.get("index_patterns") or [])
        if patterns:
            return patterns
        return [entity]

    @staticmethod
    def _template_properties(parameters: dict[str, Any]) -> dict[str, Any]:
        properties = dict(parameters.get("properties") or {})
        if properties:
            return properties
        result: dict[str, Any] = {}
        for item in list(parameters.get("keys") or []):
            field = str(item.get("field", "")).strip()
            if field:
                result[field] = {"type": "keyword"}
        return result
