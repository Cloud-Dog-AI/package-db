"""Source-connector contract for the NoSQL/search/wide-column surface (FR.SC.1).

The :class:`SourceConnector` protocol is the multi-namespace introspection + CRUD +
DDL contract used by database-exploration services (e.g. db-mcp-server). It is a
super-set of the per-collection repository protocols in
:mod:`cloud_dog_db.nosql.protocols`: it adds namespace/entity listing, schema/field
description, index listing, schema-change plan/apply and relationship inference.

Concrete implementations live in sibling modules (``mongodb``, ``couchdb``,
``couchbase``, ``cassandra``, ``elasticsearch``, ``opensearch``) and confine their
backend driver imports to that module, loaded lazily so importing this package needs
no driver installed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SourceConnector(Protocol):
    """Multi-namespace database source-connector contract (FR.SC.1).

    Mirrors the method set every NoSQL/search/wide-column backend connector exposes
    so a consuming service has one consistent introspection + CRUD + DDL surface.
    """

    def capability_report(self) -> dict[str, Any]: ...
    def validate_profile(self) -> dict[str, Any]: ...
    def list_namespaces(self) -> list[dict[str, Any]]: ...
    def list_entities(self, namespace: str) -> list[dict[str, Any]]: ...
    def describe_entity(self, namespace: str, entity: str) -> dict[str, Any]: ...
    def describe_fields(self, namespace: str, entity: str) -> dict[str, Any]: ...
    def read(
        self,
        namespace: str,
        entity: str,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...
    def create(self, namespace: str, entity: str, document: dict[str, Any]) -> dict[str, Any]: ...
    def update(self, namespace: str, entity: str, filter: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]: ...
    def delete(self, namespace: str, entity: str, filter: dict[str, Any]) -> dict[str, Any]: ...
    def count(self, namespace: str, entity: str, filter: dict[str, Any] | None = None) -> int: ...
    def sample_shapes(self, namespace: str, entity: str, n: int = 10) -> list[dict[str, Any]]: ...
    def list_indexes(self, namespace: str, entity: str) -> list[dict[str, Any]]: ...
    def schema_change_plan(self, operation: dict[str, Any]) -> dict[str, Any]: ...
    def schema_change_apply(self, plan: dict[str, Any]) -> dict[str, Any]: ...
    def extract_relationships(self, namespace: str, entity: str) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...


def json_safe(value: Any) -> Any:
    """Recursively convert backend/driver values to JSON-serialisable values.

    Handles the value types that NoSQL drivers surface (BSON ``ObjectId``/``Binary``,
    raw ``bytes``, ``datetime``/``date``, ``Decimal``, ``set``/``tuple``) so callers
    never need to import a driver to normalise a response. BSON detection is lazy and
    optional — absence of ``bson`` simply skips those branches.
    """
    # Lazy, optional BSON handling — only if pymongo/bson is installed.
    try:  # pragma: no cover - depends on optional extra
        from bson import ObjectId
        from bson.binary import Binary

        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, Binary):
            return bytes(value).hex()
    except Exception:  # pragma: no cover - bson not installed
        pass

    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    return value


def to_bson_binary(raw: bytes) -> Any:
    """Wrap raw bytes as a BSON ``Binary`` for MongoDB insertion.

    The inverse of :func:`json_safe` for binary payloads — lets a caller construct
    the driver-native binary type without importing ``bson`` itself. Requires the
    ``[mongodb]`` extra (bson ships with pymongo); imported lazily.
    """
    from bson.binary import Binary

    return Binary(raw)


def nosql_driver_exceptions() -> tuple[type[BaseException], ...]:
    """Return the tuple of backend driver exception base classes that are installed.

    Lets a consuming service catch backend driver errors (to map to its own error
    model) without importing any driver itself. Only drivers present in the
    environment contribute classes; missing extras are skipped.
    """
    exceptions: list[type[BaseException]] = []

    def _try(module: str, attr: str) -> None:
        try:  # pragma: no cover - depends on optional extras
            mod = __import__(module, fromlist=[attr])
            exc = getattr(mod, attr)
            if isinstance(exc, type) and issubclass(exc, BaseException):
                exceptions.append(exc)
        except Exception:
            pass

    _try("pymongo.errors", "PyMongoError")
    _try("cassandra", "DriverException")
    _try("elasticsearch", "ApiError")
    _try("opensearchpy.exceptions", "OpenSearchException")
    _try("couchbase.exceptions", "CouchbaseException")
    _try("requests", "RequestException")  # CouchDB REST transport
    return tuple(exceptions)
