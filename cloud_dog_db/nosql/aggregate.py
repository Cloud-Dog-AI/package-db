"""Dialect-agnostic aggregation helpers (FR.NS.6).

``aggregate`` computes a scalar aggregate over any repository exposing ``list``
(document/search/wide-column). ``aggregate_by_time_bucket`` delegates to a
repository's native time-bucket aggregation (time-series).
"""

from __future__ import annotations

from typing import Any

from cloud_dog_db.crud.repository import DBError
from cloud_dog_db.crud.specs import PageSpec, QuerySpec

_NUMERIC_OPS = ("avg", "sum", "min", "max", "count")


def aggregate(repo: Any, op: str, field: str, spec: QuerySpec | None = None) -> float | int | None:
    """Compute ``op`` over ``field`` across records matching ``spec`` (FR.NS.6)."""
    if op not in _NUMERIC_OPS:
        raise DBError(f"unsupported op '{op}' (use {_NUMERIC_OPS})")
    page = QuerySpec(filters=spec.filters if spec else [], sorts=[], page=PageSpec(limit=10**6, offset=0))
    result = repo.list(page)
    values = [item.get(field) for item in result.items if isinstance(item, dict) and item.get(field) is not None]
    if op == "count":
        return len(result.items)
    if not values:
        return None
    if op == "avg":
        return sum(values) / len(values)
    if op == "sum":
        return sum(values)
    if op == "min":
        return min(values)
    return max(values)


def aggregate_by_time_bucket(
    repo: Any,
    *,
    value_field: str,
    interval: str,
    start: Any,
    end: Any,
    op: str = "avg",
    spec: QuerySpec | None = None,
) -> list[dict[str, Any]]:
    """Delegate to a time-series repository's native bucket aggregation (FR.NS.6/FR.NS.4)."""
    if not hasattr(repo, "aggregate_by_time_bucket"):
        raise DBError("repository does not support time-bucket aggregation")
    return repo.aggregate_by_time_bucket(
        value_field=value_field, interval=interval, start=start, end=end, op=op, spec=spec
    )
