# platform-db Requirements

## Scope

`cloud_dog_db` standardizes database configuration, connectivity, migrations, CRUD, and readiness behavior for Cloud-Dog consumers.

## Functional Requirements

- Support SQLite, MariaDB/MySQL, PostgreSQL.
- Support sync and async SQLAlchemy engine/session construction.
- Provide migration runner API/CLI with `init/current/upgrade/downgrade/stamp`.
- Support project-specific Alembic version table/namespace settings.
- Provide CRUD repository primitives: create/read/update/delete, filter/sort/pagination, bulk operations.
- Provide unit-of-work transaction helper with rollback safety.
- Provide DB readiness probes including migration revision checks.
- No hardcoded credentials/DSNs in code/tests/docs/env files.
- Runtime secrets may be injected through env and Vault-resolved configuration inputs.

## Non-Functional Requirements

- Python 3.10+
- SQLAlchemy 2.x and Alembic 1.13+
- Deterministic behavior under strict test tiers (UT/ST/IT/AT)

## NoSQL / Search / Wide-Column / Vector / Time-Series Requirements (W28E-605)

Implemented in `cloud_dog_db.nosql` (optional extras; SQL surface unchanged).

| ID | Requirement | Implementation | Tests |
|---|---|---|---|
| FR.NS.1 | Generic Document Repository (MongoDB, CouchDB) | `nosql/document.py` `MongoDocumentRepository`, `CouchDocumentRepository`, `build_document_client` | `test_nosql_unit`, `test_mongodb_document_crud`, `test_couchdb_document_crud` |
| FR.NS.2 | Search Repository (OpenSearch, Elasticsearch) | `nosql/search.py` `ElasticsearchSearchRepository`, `OpenSearchSearchRepository`, `build_search_client` | `test_elasticsearch_search`, `test_opensearch_search` |
| FR.NS.3 | Wide-Column Repository (Cassandra) | `nosql/widecolumn.py` `CassandraWideColumnRepository`, `build_wide_column_client` | `test_cassandra_widecolumn_crud` |
| FR.NS.4 | TimeSeries Repository (Mongo native, OpenSearch data-stream, Postgres partition fallback) | `nosql/timeseries.py` `TimeSeriesRepository`, `build_time_series_client` | `test_timeseries_mongodb_bucket`, `test_timeseries_opensearch_bucket`, `test_timeseries_postgres_bucket` |
| FR.NS.5 | `DatabaseDialect` enum: MONGODB, COUCHDB, ELASTICSEARCH, OPENSEARCH, CASSANDRA, PGVECTOR | `config/models.py` `DatabaseDialect`, `NOSQL_DIALECTS` | `test_dialect_enum_has_nosql_values`, `test_pgvector_probe_and_store` |
| FR.NS.6 | Dialect-agnostic `aggregate()` + `aggregate_by_time_bucket()` | `nosql/aggregate.py` | `test_aggregate_helper_over_list`, `test_aggregate_by_time_bucket_requires_capable_repo` |
| FR.NS.7 | All backend config through package settings; no `os.environ.get` outside bootstrap | `nosql/settings.py` `NoSqlSettings.from_provider/from_env`; pgvector probe `nosql/vector.py` | `test_settings_*`, bespoke-grep proof |
| NF.NS.1 | Backward compatibility — SQL APIs/tests unchanged; shared error model reused | shared `DBError/ConflictError/RecordNotFoundError/TransactionError`, `QuerySpec` | `test_error_model_is_shared_backward_compatible`, full SQL suite green |
| NF.NS.2 | Optional extras per backend + combined `[nosql]` | `pyproject.toml` `[mongodb]/[couchdb]/[opensearch]/[elasticsearch]/[cassandra]/[pgvector]/[nosql]` | clean-install proof |
| NF.NS.3 | Protocols for each repository type | `nosql/protocols.py` `DocumentRepository/SearchRepository/WideColumnRepository/TimeSeriesRepository` | `test_protocol_runtime_conformance` |
| NF.NS.4 | Observability — repositories raise the shared error taxonomy; no bespoke logging | shared errors; no `logging.getLogger`/`print` (bespoke-grep) | bespoke-grep proof |
| CS.NS.1 | Vault expression discipline — credentials resolved via `cloud_dog_config`/provider mapping, never hardcoded | `NoSqlSettings.from_provider(provider)`; tests use `${vault...}` + `vault_providers` fixture | secret-scan proof, ST tests |
| CS.NS.2 | RBAC pass-through — settings carry the caller-resolved identity/credentials (no implicit elevation) | `NoSqlSettings` username/password from provider only | ST tests (auth from Vault provider) |
