"""NoSQL/search/wide-column source connectors (FR.SC.1).

Public multi-namespace introspection + CRUD + DDL connectors for MongoDB, CouchDB,
Couchbase, Cassandra, Elasticsearch and OpenSearch. Each connector lives in its own
submodule and imports its backend driver at module import time, so the connector
classes are exposed here **lazily** (PEP 562 ``__getattr__``) — importing
``cloud_dog_db.nosql.connectors`` itself pulls in no driver.

Consume either by class::

    from cloud_dog_db.nosql.connectors import MongoDBConnector
    conn = MongoDBConnector(uri="mongodb://...")

or via the dialect factory::

    from cloud_dog_db.nosql.connectors import build_source_connector
    conn = build_source_connector("mongodb", uri="mongodb://...")
"""

from __future__ import annotations

from typing import Any

from cloud_dog_db.config.models import DatabaseDialect
from cloud_dog_db.nosql.connectors.protocol import (
    SourceConnector,
    json_safe,
    nosql_driver_exceptions,
    to_bson_binary,
)

__all__ = [
    "SourceConnector",
    "json_safe",
    "nosql_driver_exceptions",
    "to_bson_binary",
    "build_source_connector",
    "MongoDBConnector",
    "CouchDBConnector",
    "CouchbaseConnector",
    "CassandraConnector",
    "ElasticsearchConnector",
    "OpenSearchConnector",
]

#: dialect -> (submodule, class name). Submodule import is deferred until use.
_CONNECTORS: dict[DatabaseDialect, tuple[str, str]] = {
    DatabaseDialect.MONGODB: ("mongodb", "MongoDBConnector"),
    DatabaseDialect.COUCHDB: ("couchdb", "CouchDBConnector"),
    DatabaseDialect.COUCHBASE: ("couchbase", "CouchbaseConnector"),
    DatabaseDialect.CASSANDRA: ("cassandra", "CassandraConnector"),
    DatabaseDialect.ELASTICSEARCH: ("elasticsearch", "ElasticsearchConnector"),
    DatabaseDialect.OPENSEARCH: ("opensearch", "OpenSearchConnector"),
}

#: class name -> submodule, for lazy attribute access.
_CLASS_TO_MODULE = {cls: mod for mod, cls in _CONNECTORS.values()}


def _load(module: str, cls: str) -> type:
    import importlib

    mod = importlib.import_module(f"{__name__}.{module}")
    return getattr(mod, cls)


def __getattr__(name: str) -> Any:  # PEP 562 — lazy connector class access
    module = _CLASS_TO_MODULE.get(name)
    if module is not None:
        return _load(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_source_connector(dialect: Any, **kwargs: Any) -> SourceConnector:
    """Construct a source connector for ``dialect`` (FR.SC.1).

    ``kwargs`` are forwarded to the connector constructor. When a ``uri`` is supplied
    and the connector exposes a ``from_uri`` classmethod (Cassandra, Couchbase) it is
    used; otherwise ``uri``/host params are passed through to ``__init__``.
    """
    key = dialect if isinstance(dialect, DatabaseDialect) else DatabaseDialect(str(dialect).strip().lower())
    if key not in _CONNECTORS:
        raise ValueError(f"{dialect} is not a NoSQL source-connector dialect")
    module, cls_name = _CONNECTORS[key]
    cls = _load(module, cls_name)

    uri = kwargs.get("uri")
    if uri is not None and hasattr(cls, "from_uri"):
        extra = {k: v for k, v in kwargs.items() if k != "uri"}
        return cls.from_uri(uri, **extra)
    return cls(**kwargs)
