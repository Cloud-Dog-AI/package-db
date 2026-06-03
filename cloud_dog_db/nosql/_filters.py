"""Shared translation of :class:`cloud_dog_db.crud.specs` query specs to backend forms."""

from __future__ import annotations

import re
from typing import Any

from cloud_dog_db.crud.specs import FilterOperator, QuerySpec


def _like_to_regex(value: str) -> str:
    # SQL LIKE wildcards -> regex.
    escaped = re.escape(str(value))
    return "^" + escaped.replace("\\%", ".*").replace("\\_", ".") + "$"


def to_mongo_filter(spec: QuerySpec | None) -> dict[str, Any]:
    """Translate filters to a MongoDB query mapping."""
    query: dict[str, Any] = {}
    if not spec:
        return query
    for f in spec.filters:
        field = "_id" if f.field == "id" else f.field
        if f.operator == FilterOperator.EQ:
            query[field] = f.value
        elif f.operator == FilterOperator.NE:
            query[field] = {"$ne": f.value}
        elif f.operator == FilterOperator.GT:
            query[field] = {"$gt": f.value}
        elif f.operator == FilterOperator.GTE:
            query[field] = {"$gte": f.value}
        elif f.operator == FilterOperator.LT:
            query[field] = {"$lt": f.value}
        elif f.operator == FilterOperator.LTE:
            query[field] = {"$lte": f.value}
        elif f.operator == FilterOperator.IN:
            query[field] = {"$in": list(f.value)}
        elif f.operator == FilterOperator.LIKE:
            query[field] = {"$regex": _like_to_regex(f.value)}
        elif f.operator == FilterOperator.ILIKE:
            query[field] = {"$regex": _like_to_regex(f.value), "$options": "i"}
        elif f.operator == FilterOperator.IS_NULL:
            query[field] = None if f.value else {"$ne": None}
    return query


def to_mongo_sort(spec: QuerySpec | None) -> list[tuple[str, int]]:
    if not spec:
        return []
    return [("_id" if s.field == "id" else s.field, -1 if s.descending else 1) for s in spec.sorts]


def to_es_query(spec: QuerySpec | None) -> dict[str, Any]:
    """Translate filters to an Elasticsearch/OpenSearch bool query."""
    if not spec or not spec.filters:
        return {"match_all": {}}
    must: list[dict[str, Any]] = []
    must_not: list[dict[str, Any]] = []
    for f in spec.filters:
        field = f.field
        if f.operator == FilterOperator.EQ:
            must.append({"term": {field: f.value}})
        elif f.operator == FilterOperator.NE:
            must_not.append({"term": {field: f.value}})
        elif f.operator == FilterOperator.GT:
            must.append({"range": {field: {"gt": f.value}}})
        elif f.operator == FilterOperator.GTE:
            must.append({"range": {field: {"gte": f.value}}})
        elif f.operator == FilterOperator.LT:
            must.append({"range": {field: {"lt": f.value}}})
        elif f.operator == FilterOperator.LTE:
            must.append({"range": {field: {"lte": f.value}}})
        elif f.operator == FilterOperator.IN:
            must.append({"terms": {field: list(f.value)}})
        elif f.operator in (FilterOperator.LIKE, FilterOperator.ILIKE):
            must.append({"wildcard": {field: str(f.value).replace("%", "*").replace("_", "?")}})
        elif f.operator == FilterOperator.IS_NULL:
            clause = {"exists": {"field": field}}
            (must_not if f.value else must).append(clause)
    return {"bool": {"must": must or [{"match_all": {}}], "must_not": must_not}}


def to_es_sort(spec: QuerySpec | None) -> list[dict[str, Any]]:
    if not spec:
        return []
    return [{s.field: {"order": "desc" if s.descending else "asc"}} for s in spec.sorts]
