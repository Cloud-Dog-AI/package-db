"""cloud_dog_db NoSQL / search / wide-column / vector / time-series surface.

Public exports mirror the SQL surface contract. Driver dependencies are optional
extras and loaded lazily, so importing this package needs no NoSQL driver installed.
"""

from cloud_dog_db.config.models import NOSQL_DIALECTS, DatabaseDialect
from cloud_dog_db.crud.repository import ConflictError, DBError, RecordNotFoundError, TransactionError
from cloud_dog_db.crud.specs import (
    FilterOperator,
    FilterSpec,
    PageResult,
    PageSpec,
    QuerySpec,
    SortSpec,
)
from cloud_dog_db.nosql.aggregate import aggregate, aggregate_by_time_bucket
from cloud_dog_db.nosql.document import (
    CouchDocumentRepository,
    MongoDocumentRepository,
    build_document_client,
)
from cloud_dog_db.nosql.protocols import (
    Document,
    DocumentRepository,
    SearchRepository,
    TimeSeriesRepository as TimeSeriesRepositoryProtocol,
    WideColumnRepository,
)
from cloud_dog_db.nosql.search import (
    ElasticsearchSearchRepository,
    OpenSearchSearchRepository,
    build_search_client,
)
from cloud_dog_db.nosql.settings import NoSqlSettings
from cloud_dog_db.nosql.timeseries import TimeSeriesRepository, build_time_series_client
from cloud_dog_db.nosql.vector import (
    PgVectorStore,
    build_vector_client,
    ensure_vector_extension,
    probe_pgvector,
)
from cloud_dog_db.nosql.connectors import (
    SourceConnector,
    build_source_connector,
    json_safe,
    nosql_driver_exceptions,
)
from cloud_dog_db.nosql.widecolumn import (
    CassandraWideColumnRepository,
    build_wide_column_client,
)

#: Source-connector classes are exposed lazily (PEP 562) — each pulls in its backend
#: driver only on first access, so ``import cloud_dog_db.nosql`` stays driver-free.
_SOURCE_CONNECTOR_NAMES = {
    "MongoDBConnector",
    "CouchDBConnector",
    "CouchbaseConnector",
    "CassandraConnector",
    "ElasticsearchConnector",
    "OpenSearchConnector",
}


def __getattr__(name: str):  # PEP 562 — defer driver imports to first use
    if name in _SOURCE_CONNECTOR_NAMES:
        from cloud_dog_db.nosql import connectors

        return getattr(connectors, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "NOSQL_DIALECTS",
    "DatabaseDialect",
    "NoSqlSettings",
    "Document",
    "DocumentRepository",
    "SearchRepository",
    "WideColumnRepository",
    "TimeSeriesRepositoryProtocol",
    "MongoDocumentRepository",
    "CouchDocumentRepository",
    "ElasticsearchSearchRepository",
    "OpenSearchSearchRepository",
    "CassandraWideColumnRepository",
    "TimeSeriesRepository",
    "PgVectorStore",
    "build_document_client",
    "build_search_client",
    "build_wide_column_client",
    "build_time_series_client",
    "build_vector_client",
    "probe_pgvector",
    "ensure_vector_extension",
    "aggregate",
    "aggregate_by_time_bucket",
    # shared error model + specs (NF.NS.1 backward compatibility)
    "DBError",
    "ConflictError",
    "RecordNotFoundError",
    "TransactionError",
    "FilterOperator",
    "FilterSpec",
    "SortSpec",
    "PageSpec",
    "QuerySpec",
    "PageResult",
]
