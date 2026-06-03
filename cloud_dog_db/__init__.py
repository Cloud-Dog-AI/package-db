"""cloud_dog_db public exports."""

from cloud_dog_db.config.models import NOSQL_DIALECTS, DatabaseDialect, DatabaseSettings
from cloud_dog_db.nosql import (
    NoSqlSettings,
    build_document_client,
    build_search_client,
    build_time_series_client,
    build_vector_client,
    build_wide_column_client,
    probe_pgvector,
)
from cloud_dog_db.crud.repository import (
    ConflictError,
    DBError,
    RecordNotFoundError,
    Repository,
    TransactionError,
    UnitOfWork,
)
from cloud_dog_db.crud.specs import FilterOperator, FilterSpec, PageResult, PageSpec, QuerySpec, SortSpec
from cloud_dog_db.engine.factory import build_async_engine, build_sync_engine
from cloud_dog_db.models.base import PlatformBase, naming_convention
from cloud_dog_db.models.mixins import AuditMixin, SoftDeleteMixin, TenantMixin, TimestampMixin
from cloud_dog_db.health.probes import check_migration_revision, probe_database, require_revision
from cloud_dog_db.migrations.runner import MigrationRunner
from cloud_dog_db.session.session_manager import AsyncSessionManager, SyncSessionManager

__all__ = [
    "AuditMixin",
    "AsyncSessionManager",
    "ConflictError",
    "DBError",
    "DatabaseDialect",
    "DatabaseSettings",
    "NOSQL_DIALECTS",
    "NoSqlSettings",
    "build_document_client",
    "build_search_client",
    "build_wide_column_client",
    "build_time_series_client",
    "build_vector_client",
    "probe_pgvector",
    "FilterOperator",
    "FilterSpec",
    "MigrationRunner",
    "PlatformBase",
    "PageResult",
    "PageSpec",
    "QuerySpec",
    "RecordNotFoundError",
    "SoftDeleteMixin",
    "Repository",
    "SortSpec",
    "SyncSessionManager",
    "TenantMixin",
    "TimestampMixin",
    "TransactionError",
    "UnitOfWork",
    "build_async_engine",
    "naming_convention",
    "build_sync_engine",
    "check_migration_revision",
    "probe_database",
    "require_revision",
]
