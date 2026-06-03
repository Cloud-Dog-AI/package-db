"""Query specification models for repositories."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar


class FilterOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    LIKE = "like"
    ILIKE = "ilike"
    IS_NULL = "is_null"


@dataclass(slots=True)
class FilterSpec:
    field: str
    operator: FilterOperator = FilterOperator.EQ
    value: Any = None


@dataclass(slots=True)
class SortSpec:
    field: str
    descending: bool = False


@dataclass(slots=True)
class PageSpec:
    limit: int = 50
    offset: int = 0


@dataclass(slots=True)
class QuerySpec:
    filters: list[FilterSpec] = field(default_factory=list)
    sorts: list[SortSpec] = field(default_factory=list)
    page: PageSpec | None = None


T = TypeVar("T")


@dataclass(slots=True)
class PageResult(Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
