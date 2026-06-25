# platform-db Test Plan

## Test Tiers

- **UT**
  - Config parsing
  - DSN/dialect resolution
  - CRUD base behavior and error mapping
  - Migration runner command wiring
- **ST**
  - SQLite file lifecycle: migrate, CRUD, rollback behavior
- **IT**
  - Real MariaDB/MySQL migrate+CRUD+rollback
  - Real PostgreSQL migrate+CRUD+rollback
  - SQLite integration parity run
- **AT**
  - Consumer-style end-to-end flow using package abstractions only

## Mandatory Invocation

```bash
pytest tests/unit/ -q --env tests/env-UT
pytest tests/system/ -q --env tests/env-ST
pytest tests/integration/ -q --env tests/env-IT-sqlite
pytest tests/integration/ -q --env tests/env-IT-mariadb
pytest tests/integration/ -q --env tests/env-IT-postgres
pytest tests/application/ -q --env tests/env-AT
```

## NoSQL Tiers (W28E-605)

- **UT** (`tests/unit/test_nosql_unit.py`) — dialects, `NoSqlSettings`, filter translation, factory dispatch, protocols, error model, aggregate. No backend.
- **ST** (`tests/system/test_nosql_st.py`) — REAL backends (no mocks, assert real content):
  - MongoDB (mongo0), CouchDB (couchdb0), OpenSearch (opensearch0:1201), Cassandra (cassandra0) via `dev.databases.providers.*`.
  - Elasticsearch via a local container (elastic0 master is disk-blocked/stuck — pending tasks >22d; see evidence) — W28E-605 local-container allowance.
  - pgvector via a local `pgvector/pgvector:pg16` container (shared db2 lacks the `vector` extension control file) — local-container allowance.
  - Time-series buckets: Mongo native, OpenSearch date-histogram, Postgres (db2) date_trunc.

```bash
source /opt/iac/Development/cloud-dog-ai/env-vault
pytest tests/unit/test_nosql_unit.py -q --env tests/env-UT
pytest tests/system/test_nosql_st.py -q --env tests/env-ST
```
