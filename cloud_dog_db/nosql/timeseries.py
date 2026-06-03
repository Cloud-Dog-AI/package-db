"""TimeSeries repository (FR.NS.4) — Mongo native, OpenSearch data-stream, Postgres partition fallback."""

from __future__ import annotations

from typing import Any

from cloud_dog_db.config.models import DatabaseDialect
from cloud_dog_db.crud.repository import DBError
from cloud_dog_db.crud.specs import QuerySpec
from cloud_dog_db.nosql.settings import NoSqlSettings

#: interval token -> (unit, size) for the supported backends.
_INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("minute", 1), "5m": ("minute", 5), "1h": ("hour", 1), "6h": ("hour", 6), "1d": ("day", 1),
    "minute": ("minute", 1), "hour": ("hour", 1), "day": ("day", 1),
}
_ES_INTERVAL = {"minute": "m", "hour": "h", "day": "d"}
_AGG = ("avg", "sum", "min", "max", "count")


def _interval(token: str) -> tuple[str, int]:
    if token not in _INTERVALS:
        raise DBError(f"unsupported interval '{token}' (use {sorted(_INTERVALS)})")
    return _INTERVALS[token]


class TimeSeriesRepository:
    """Dialect-agnostic time-series repository."""

    def __init__(self, settings: NoSqlSettings, measurement: str, *, time_field: str = "ts", client: Any = None):
        self._settings = settings
        self._measurement = measurement
        self._time_field = time_field
        self._client = client
        self._dialect = settings.dialect
        if self._dialect == DatabaseDialect.MONGODB:
            from pymongo import MongoClient

            self._owns = client is None
            self._client = client or MongoClient(
                host=settings.host, port=settings.port, username=settings.username,
                password=settings.password_plain(), serverSelectionTimeoutMS=settings.timeout_seconds * 1000,
            )
            self._col = self._client[settings.database or "cloud_dog_ts"][measurement]
        elif self._dialect == DatabaseDialect.OPENSEARCH:
            from opensearchpy import OpenSearch

            self._owns = client is None
            self._client = client or OpenSearch(
                hosts=[{"host": settings.host, "port": settings.port}],
                http_auth=(settings.username, settings.password_plain()) if settings.username else None,
                use_ssl=settings.use_ssl, verify_certs=settings.verify_certs, ssl_show_warn=False,
                timeout=settings.timeout_seconds,
            )
            self._index = measurement
        elif self._dialect in {DatabaseDialect.POSTGRESQL, DatabaseDialect.PGVECTOR}:
            import psycopg

            self._owns = client is None
            self._client = client or psycopg.connect(
                host=settings.host, port=settings.port, user=settings.username,
                password=settings.password_plain(), dbname=settings.database or "postgres",
                connect_timeout=settings.timeout_seconds,
            )
        else:
            raise DBError(f"{self._dialect} does not support the time-series surface")

    # ── write ────────────────────────────────────────────────────────────
    def record(self, point: dict[str, Any]) -> dict[str, Any]:
        self.record_many([point])
        return dict(point)

    def record_many(self, points: list[dict[str, Any]]) -> int:
        if not points:
            return 0
        if self._dialect == DatabaseDialect.MONGODB:
            self._col.insert_many([dict(p) for p in points])
        elif self._dialect == DatabaseDialect.OPENSEARCH:
            if not self._client.indices.exists(index=self._index):
                self._client.indices.create(index=self._index)
            for p in points:
                self._client.index(index=self._index, body=dict(p), refresh=True)
        else:  # postgres
            with self._client.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._measurement} "
                    f"({self._time_field} TIMESTAMPTZ NOT NULL, tags JSONB DEFAULT '{{}}', value DOUBLE PRECISION)"
                )
                for p in points:
                    cur.execute(
                        f"INSERT INTO {self._measurement} ({self._time_field}, value) VALUES (%s, %s)",
                        (p[self._time_field], p.get("value")),
                    )
                self._client.commit()
        return len(points)

    # ── read ─────────────────────────────────────────────────────────────
    def range(self, start: Any, end: Any, spec: QuerySpec | None = None) -> list[dict[str, Any]]:
        if self._dialect == DatabaseDialect.MONGODB:
            from cloud_dog_db.nosql._filters import to_mongo_filter

            q = to_mongo_filter(spec)
            q[self._time_field] = {"$gte": start, "$lte": end}
            return [{k: (str(v) if k == "_id" else v) for k, v in d.items()} for d in self._col.find(q)]
        if self._dialect == DatabaseDialect.OPENSEARCH:
            body = {"query": {"range": {self._time_field: {"gte": start, "lte": end}}}, "size": 1000}
            resp = self._client.search(index=self._index, body=body)
            return [{"id": h["_id"], **h["_source"]} for h in resp["hits"]["hits"]]
        with self._client.cursor() as cur:
            cur.execute(
                f"SELECT {self._time_field}, value FROM {self._measurement} "
                f"WHERE {self._time_field} BETWEEN %s AND %s ORDER BY {self._time_field}",
                (start, end),
            )
            return [{self._time_field: r[0], "value": r[1]} for r in cur.fetchall()]

    def aggregate_by_time_bucket(
        self, *, value_field: str, interval: str, start: Any, end: Any, op: str = "avg",
        spec: QuerySpec | None = None,
    ) -> list[dict[str, Any]]:
        if op not in _AGG:
            raise DBError(f"unsupported op '{op}' (use {_AGG})")
        unit, size = _interval(interval)
        if self._dialect == DatabaseDialect.MONGODB:
            acc = {"avg": "$avg", "sum": "$sum", "min": "$min", "max": "$max"}.get(op)
            group_val = {acc: f"${value_field}"} if acc else {"$sum": 1}
            pipeline = [
                {"$match": {self._time_field: {"$gte": start, "$lte": end}}},
                {"$group": {
                    "_id": {"$dateTrunc": {"date": f"${self._time_field}", "unit": unit, "binSize": size}},
                    "value": group_val,
                }},
                {"$sort": {"_id": 1}},
            ]
            return [{"bucket": d["_id"], "value": d["value"]} for d in self._col.aggregate(pipeline)]
        if self._dialect == DatabaseDialect.OPENSEARCH:
            metric = {"count": {"value_count": {"field": value_field}}}.get(op) or {op: {"field": value_field}}
            body = {
                "size": 0,
                "query": {"range": {self._time_field: {"gte": start, "lte": end}}},
                "aggs": {"buckets": {
                    "date_histogram": {"field": self._time_field, "fixed_interval": f"{size}{_ES_INTERVAL[unit]}"},
                    "aggs": {"metric": metric},
                }},
            }
            resp = self._client.search(index=self._index, body=body)
            out = []
            for b in resp["aggregations"]["buckets"]["buckets"]:
                val = b["doc_count"] if op == "count" else b["metric"]["value"]
                out.append({"bucket": b["key_as_string"], "value": val})
            return out
        # postgres date_trunc fallback
        agg_sql = {"avg": "AVG", "sum": "SUM", "min": "MIN", "max": "MAX", "count": "COUNT"}[op]
        with self._client.cursor() as cur:
            cur.execute(
                f"SELECT date_trunc(%s, {self._time_field}) AS bucket, {agg_sql}({value_field}) AS value "
                f"FROM {self._measurement} WHERE {self._time_field} BETWEEN %s AND %s "
                f"GROUP BY bucket ORDER BY bucket",
                (unit, start, end),
            )
            return [{"bucket": r[0], "value": float(r[1]) if r[1] is not None else None} for r in cur.fetchall()]

    def close(self) -> None:
        if getattr(self, "_owns", False):
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass


def build_time_series_client(settings: NoSqlSettings, *, measurement: str, time_field: str = "ts") -> TimeSeriesRepository:
    """Factory dispatch for the time-series repository (FR.NS.4)."""
    return TimeSeriesRepository(settings, measurement=measurement, time_field=time_field)
