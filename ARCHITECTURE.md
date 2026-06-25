# platform-db Architecture

## Modules

- `cloud_dog_db.config.models`
  - Typed DB settings model and environment binding.
- `cloud_dog_db.engine.factory`
  - Sync/async engine builders with dialect-aware defaults.
- `cloud_dog_db.session.session_manager`
  - Session managers and transactional unit-of-work contexts.
- `cloud_dog_db.migrations.runner`
  - Alembic-backed migration orchestration and CLI.
- `cloud_dog_db.crud.specs`
  - Query/filter/sort/pagination specs.
- `cloud_dog_db.crud.repository`
  - Generic repository and DB error taxonomy.
- `cloud_dog_db.health.probes`
  - Readiness/liveness probes and migration guards.

## Request Flow (Consumer)

1. Consumer builds `DatabaseSettings` from env/config.
2. Engine/session manager created per sync/async execution path.
3. Startup optionally executes migration bootstrap (`upgrade head`).
4. Repository and unit-of-work abstractions handle business persistence.
5. Probes validate `SELECT 1` and migration revision compliance.

## Migration Design

- Alembic `Config` object is built at runtime.
- `version_table` and optional `version_table_schema` are injectable.
- Script location can be package templates or project migration directory.

## NoSQL / Search / Wide-Column / Vector / Time-Series Surface (W28E-605)

- `cloud_dog_db.nosql.settings` — `NoSqlSettings` (mirrors `DatabaseSettings`); `from_provider(vault_provider)` / `from_env`.
- `cloud_dog_db.nosql.protocols` — `DocumentRepository`, `SearchRepository`, `WideColumnRepository`, `TimeSeriesRepository` Protocols (mirror SQL `Repository` contract: `create/get/update/delete/list -> PageResult`).
- `cloud_dog_db.nosql.document` — MongoDB + CouchDB document repositories + `build_document_client`.
- `cloud_dog_db.nosql.search` — Elasticsearch + OpenSearch search repositories (shared base) + `build_search_client`.
- `cloud_dog_db.nosql.widecolumn` — Cassandra wide-column repository + `build_wide_column_client`.
- `cloud_dog_db.nosql.vector` — `probe_pgvector`, `ensure_vector_extension`, `PgVectorStore` (psycopg-based).
- `cloud_dog_db.nosql.timeseries` — `TimeSeriesRepository` (Mongo `$dateTrunc`, OpenSearch `date_histogram`, Postgres `date_trunc`).
- `cloud_dog_db.nosql.aggregate` — dialect-agnostic `aggregate` / `aggregate_by_time_bucket`.
- `cloud_dog_db.nosql._filters` — `QuerySpec` → Mongo / Elasticsearch query translation.

Design rules: driver imports (`pymongo`/`couchdb`/`elasticsearch`/`opensearchpy`/`cassandra`/`psycopg`) are confined to `cloud_dog_db.nosql.*` and loaded lazily, so importing the package needs no driver and each backend is an optional extra. The shared SQL error taxonomy and query specs are reused (NF.NS.1).
