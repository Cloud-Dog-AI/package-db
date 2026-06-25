# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""ST against REAL Vault-configured backends (RULES §5.5 — no mocks; assert real content).

Run: source env-vault; pytest tests/system/test_nosql_st.py --env tests/env-ST
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from cloud_dog_db import NoSqlSettings, build_document_client, build_search_client, probe_pgvector
from cloud_dog_db.crud.repository import RecordNotFoundError
from cloud_dog_db.crud.specs import FilterOperator, FilterSpec, QuerySpec
from cloud_dog_db.nosql.timeseries import TimeSeriesRepository
from cloud_dog_db.nosql.vector import PgVectorStore
from cloud_dog_db.nosql.widecolumn import CassandraWideColumnRepository

SUFFIX = uuid.uuid4().hex[:8]


def _settings(providers: dict, name: str, **overrides) -> NoSqlSettings:
    return NoSqlSettings.from_provider(providers[name], **overrides)


def test_mongodb_document_crud(vault_providers) -> None:  # FR.NS.1
    repo = build_document_client(
        _settings(vault_providers, "mongodb"), database="cloud_dog_db_test", collection=f"w28e605_{SUFFIX}"
    )
    try:
        created = repo.create({"id": "m1", "status": "active", "score": 7})
        assert created["id"] == "m1"
        assert repo.get("m1")["status"] == "active"
        repo.create({"id": "m2", "status": "active", "score": 3})
        repo.create({"id": "m3", "status": "archived", "score": 9})
        updated = repo.update("m1", {"score": 10})
        assert updated["score"] == 10
        page = repo.list(QuerySpec(filters=[FilterSpec("status", FilterOperator.EQ, "active")]))
        assert page.total == 2 and {i["id"] for i in page.items} == {"m1", "m2"}
        assert repo.count() == 3
        repo.delete("m1")
        with pytest.raises(RecordNotFoundError):
            repo.get("m1")
    finally:
        repo._collection.drop()
        repo.close()


def test_couchdb_document_crud(vault_providers) -> None:  # FR.NS.1
    repo = build_document_client(_settings(vault_providers, "couchdb"), database=f"w28e605_{SUFFIX}")
    try:
        repo.create({"id": "c1", "status": "active", "score": 5})
        repo.create({"id": "c2", "status": "archived", "score": 8})
        assert repo.get("c1")["status"] == "active"
        repo.update("c1", {"score": 11})
        assert repo.get("c1")["score"] == 11
        page = repo.list(QuerySpec(filters=[FilterSpec("status", FilterOperator.EQ, "active")]))
        assert page.total == 1 and page.items[0]["id"] == "c1"
        repo.delete("c2")
        with pytest.raises(RecordNotFoundError):
            repo.get("c2")
    finally:
        repo._server.delete(f"w28e605_{SUFFIX}")


def _exercise_search_repo(repo) -> None:
    try:
        repo.index("s1", {"title": "alpha report", "status": "active"})
        repo.index("s2", {"title": "beta report", "status": "archived"})
        repo.refresh()
        assert repo.get("s1")["title"] == "alpha report"
        hits = repo.search({"match": {"title": "alpha"}})
        assert hits.total >= 1 and any(h["title"] == "alpha report" for h in hits.items)
        listed = repo.list(QuerySpec(filters=[FilterSpec("status", FilterOperator.EQ, "active")]))
        assert any(h["id"] == "s1" for h in listed.items)
        repo.delete("s1")
        with pytest.raises(RecordNotFoundError):
            repo.get("s1")
    finally:
        try:
            repo._client.indices.delete(index=repo._index)
        finally:
            repo.close()


def test_elasticsearch_search(local_elasticsearch_settings) -> None:  # FR.NS.2 (local ES — elastic0 master stuck)
    _exercise_search_repo(build_search_client(local_elasticsearch_settings, index=f"w28e605_{SUFFIX}"))


def test_opensearch_search(vault_providers) -> None:  # FR.NS.2 (real preprod opensearch0:1201)
    _exercise_search_repo(build_search_client(_settings(vault_providers, "opensearch"), index=f"w28e605_{SUFFIX}"))


def test_cassandra_widecolumn_crud(vault_providers) -> None:  # FR.NS.3
    ks = f"w28e605_{SUFFIX}"
    settings = _settings(vault_providers, "cassandra")
    repo = CassandraWideColumnRepository(settings, keyspace=ks, table="events", key_columns=["id"], session=None)
    try:
        repo.execute(
            f"CREATE KEYSPACE IF NOT EXISTS {ks} WITH replication = "
            "{'class':'SimpleStrategy','replication_factor':1}"
        )
        repo.execute(f"CREATE TABLE IF NOT EXISTS {ks}.events (id text PRIMARY KEY, status text, score int)")
        repo.create({"id": "e1", "status": "active", "score": 4})
        repo.create({"id": "e2", "status": "archived", "score": 9})
        assert repo.get({"id": "e1"})["status"] == "active"
        repo.update({"id": "e1"}, {"score": 12})
        assert repo.get({"id": "e1"})["score"] == 12
        page = repo.list(QuerySpec(filters=[FilterSpec("status", FilterOperator.EQ, "active")]))
        assert any(r["id"] == "e1" for r in page.items)
        repo.delete({"id": "e2"})
        with pytest.raises(RecordNotFoundError):
            repo.get({"id": "e2"})
    finally:
        try:
            repo.execute(f"DROP KEYSPACE IF EXISTS {ks}")
        finally:
            repo.close()


def test_pgvector_probe_and_store(pgvector_settings) -> None:  # FR.NS.5/FR.NS.7
    settings = pgvector_settings
    probe = probe_pgvector(settings)
    assert probe["ok"] is True
    assert probe["extension_available"] is True, f"pgvector extension unavailable: {probe}"
    store = PgVectorStore(settings, table=f"w28e605_{SUFFIX}", dim=3)
    try:
        store.create_table()
        store.upsert("v1", [0.1, 0.2, 0.3], {"k": "a"})
        store.upsert("v2", [0.9, 0.8, 0.7], {"k": "b"})
        assert store.get("v1")["metadata"]["k"] == "a"
        results = store.search([0.1, 0.2, 0.25], k=2)
        assert results and results[0]["id"] == "v1"
    finally:
        with store._conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS w28e605_{SUFFIX}")
            store._conn.commit()
        store.close()


def _ts_window():
    base = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    points = [{"ts": base + dt.timedelta(minutes=i), "value": float(i)} for i in range(6)]
    return base, points


def test_timeseries_mongodb_bucket(vault_providers) -> None:  # FR.NS.4 mongo native
    settings = _settings(vault_providers, "mongodb", database="cloud_dog_db_test")
    repo = TimeSeriesRepository(settings, measurement=f"ts_{SUFFIX}", time_field="ts")
    base, points = _ts_window()
    try:
        assert repo.record_many(points) == 6
        buckets = repo.aggregate_by_time_bucket(
            value_field="value", interval="5m", start=base, end=base + dt.timedelta(minutes=10), op="avg"
        )
        assert buckets and all("bucket" in b and "value" in b for b in buckets)
    finally:
        repo._col.drop()
        repo.close()


def test_timeseries_opensearch_bucket(vault_providers) -> None:  # FR.NS.4 opensearch data-stream
    settings = _settings(vault_providers, "opensearch")
    repo = TimeSeriesRepository(settings, measurement=f"ts_{SUFFIX}", time_field="ts")
    base, points = _ts_window()
    iso = [{"ts": p["ts"].isoformat(), "value": p["value"]} for p in points]
    try:
        assert repo.record_many(iso) == 6
        buckets = repo.aggregate_by_time_bucket(
            value_field="value", interval="5m", start=base.isoformat(),
            end=(base + dt.timedelta(minutes=10)).isoformat(), op="avg",
        )
        assert buckets and all("bucket" in b for b in buckets)
    finally:
        try:
            repo._client.indices.delete(index=f"ts_{SUFFIX}")
        finally:
            repo.close()


def test_timeseries_postgres_bucket(vault_providers) -> None:  # FR.NS.4 postgres partition/date_trunc
    settings = _settings(vault_providers, "postgres", dialect="pgvector", database="postgres")
    repo = TimeSeriesRepository(settings, measurement=f"ts_{SUFFIX}", time_field="ts")
    base, points = _ts_window()
    try:
        assert repo.record_many(points) == 6
        buckets = repo.aggregate_by_time_bucket(
            value_field="value", interval="minute", start=base, end=base + dt.timedelta(minutes=10), op="avg"
        )
        assert buckets and all("bucket" in b and "value" in b for b in buckets)
    finally:
        with repo._client.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS ts_{SUFFIX}")
            repo._client.commit()
        repo.close()
