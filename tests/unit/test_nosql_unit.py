# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License").

"""UT for the cloud_dog_db NoSQL surface (no backend required) — FR.NS.1-7, NF.NS.1-3."""

from __future__ import annotations

import pytest

from cloud_dog_db import (
    NOSQL_DIALECTS,
    DatabaseDialect,
    NoSqlSettings,
    build_document_client,
    build_search_client,
    build_wide_column_client,
)
from cloud_dog_db.crud.repository import ConflictError, DBError, RecordNotFoundError, TransactionError
from cloud_dog_db.crud.specs import FilterOperator, FilterSpec, PageResult, QuerySpec, SortSpec
from cloud_dog_db.nosql._filters import to_es_query, to_es_sort, to_mongo_filter, to_mongo_sort
from cloud_dog_db.nosql.aggregate import aggregate, aggregate_by_time_bucket
from cloud_dog_db.nosql.protocols import DocumentRepository, SearchRepository, WideColumnRepository


def test_dialect_enum_has_nosql_values() -> None:  # FR.NS.5
    for name in ("MONGODB", "COUCHDB", "ELASTICSEARCH", "OPENSEARCH", "CASSANDRA", "PGVECTOR"):
        assert hasattr(DatabaseDialect, name)
    assert DatabaseDialect.MONGODB in NOSQL_DIALECTS
    assert DatabaseDialect.SQLITE not in NOSQL_DIALECTS


def test_error_model_is_shared_backward_compatible() -> None:  # NF.NS.1
    for err in (ConflictError, RecordNotFoundError, TransactionError):
        assert issubclass(err, DBError)


def test_settings_from_provider_defaults_and_ports() -> None:
    s = NoSqlSettings.from_provider(
        {"type": "mongodb", "host": "h", "username": "u", "password": "p"}
    )
    assert s.dialect == DatabaseDialect.MONGODB
    assert s.port == 27017
    assert s.password_plain() == "p"
    s2 = NoSqlSettings.from_provider({"type": "opensearch", "host": "h", "port": "1201"})
    assert s2.dialect == DatabaseDialect.OPENSEARCH
    assert s2.port == 1201


def test_settings_rejects_non_nosql_dialect() -> None:
    with pytest.raises(ValueError):
        NoSqlSettings(dialect=DatabaseDialect.SQLITE)


def test_settings_dialect_aliases() -> None:
    assert NoSqlSettings(dialect="mongo").dialect == DatabaseDialect.MONGODB
    assert NoSqlSettings(dialect="es").dialect == DatabaseDialect.ELASTICSEARCH


def test_mongo_filter_translation() -> None:  # FR.NS.1
    spec = QuerySpec(
        filters=[
            FilterSpec("status", FilterOperator.EQ, "active"),
            FilterSpec("score", FilterOperator.GTE, 5),
            FilterSpec("tag", FilterOperator.IN, ["a", "b"]),
            FilterSpec("name", FilterOperator.ILIKE, "ab%"),
        ],
        sorts=[SortSpec("score", descending=True)],
    )
    q = to_mongo_filter(spec)
    assert q["status"] == "active"
    assert q["score"] == {"$gte": 5}
    assert q["tag"] == {"$in": ["a", "b"]}
    assert q["name"]["$options"] == "i"
    assert to_mongo_sort(spec) == [("score", -1)]


def test_es_query_translation() -> None:  # FR.NS.2
    spec = QuerySpec(
        filters=[
            FilterSpec("status", FilterOperator.EQ, "active"),
            FilterSpec("score", FilterOperator.LT, 9),
        ],
        sorts=[SortSpec("ts", descending=False)],
    )
    body = to_es_query(spec)
    assert {"term": {"status": "active"}} in body["bool"]["must"]
    assert {"range": {"score": {"lt": 9}}} in body["bool"]["must"]
    assert to_es_sort(spec) == [{"ts": {"order": "asc"}}]


def test_factory_dispatch_rejects_wrong_dialect() -> None:
    with pytest.raises(DBError):
        build_document_client(NoSqlSettings(dialect=DatabaseDialect.ELASTICSEARCH))
    with pytest.raises(DBError):
        build_search_client(NoSqlSettings(dialect=DatabaseDialect.MONGODB))
    with pytest.raises(DBError):
        build_wide_column_client(
            NoSqlSettings(dialect=DatabaseDialect.MONGODB), keyspace="k", table="t", key_columns=["id"]
        )


class _FakeDocRepo:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def create(self, document): ...  # noqa: D401
    def get(self, record_id): ...
    def update(self, record_id, payload): ...
    def delete(self, record_id): ...
    def count(self, spec=None): ...
    def close(self): ...

    def list(self, spec=None) -> PageResult:
        return PageResult(items=list(self._rows), total=len(self._rows), limit=1000, offset=0)


def test_protocol_runtime_conformance() -> None:  # NF.NS.3
    assert isinstance(_FakeDocRepo([]), DocumentRepository)
    assert not isinstance(object(), SearchRepository)
    assert not isinstance(object(), WideColumnRepository)


def test_aggregate_helper_over_list() -> None:  # FR.NS.6
    repo = _FakeDocRepo([{"v": 2}, {"v": 4}, {"v": 6}])
    assert aggregate(repo, "avg", "v") == 4
    assert aggregate(repo, "sum", "v") == 12
    assert aggregate(repo, "min", "v") == 2
    assert aggregate(repo, "max", "v") == 6
    assert aggregate(repo, "count", "v") == 3
    with pytest.raises(DBError):
        aggregate(repo, "median", "v")


def test_aggregate_by_time_bucket_requires_capable_repo() -> None:
    with pytest.raises(DBError):
        aggregate_by_time_bucket(_FakeDocRepo([]), value_field="v", interval="1h", start=0, end=1)
