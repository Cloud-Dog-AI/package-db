"""Generic repository primitives and transactional helpers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from cloud_dog_db.crud.specs import FilterOperator, FilterSpec, PageResult, PageSpec, QuerySpec, SortSpec


class DBError(RuntimeError):
    """Base class for repository-level DB errors."""


class ConflictError(DBError):
    """Raised for uniqueness and conflict violations."""


class RecordNotFoundError(DBError):
    """Raised when an expected record does not exist."""


class TransactionError(DBError):
    """Raised when transaction execution fails."""


ModelT = TypeVar("ModelT")


class Repository(Generic[ModelT]):
    """SQLAlchemy repository with filtering/sorting/pagination helpers."""

    def __init__(self, model: type[ModelT], session: Session):
        self.model = model
        self.session = session

    def create(self, payload: dict[str, Any]) -> ModelT:
        instance = self.model(**payload)
        self.session.add(instance)
        self._flush()
        return instance

    def get(self, record_id: Any) -> ModelT:
        instance = self.session.get(self.model, record_id)
        if instance is None:
            raise RecordNotFoundError(f"{self.model.__name__}({record_id}) not found")
        return instance

    def delete(self, record_id: Any) -> None:
        instance = self.get(record_id)
        self.session.delete(instance)
        self._flush()

    def update(self, record_id: Any, payload: dict[str, Any]) -> ModelT:
        instance = self.get(record_id)
        for key, value in payload.items():
            setattr(instance, key, value)
        self._flush()
        return instance

    def list(self, spec: QuerySpec | None = None) -> PageResult[ModelT]:
        resolved = spec or QuerySpec(page=PageSpec())
        stmt = select(self.model)
        stmt = self._apply_filters(stmt, resolved.filters)
        stmt = self._apply_sorts(stmt, resolved.sorts)

        count_stmt = select(func.count()).select_from(self.model)
        count_stmt = self._apply_filters(count_stmt, resolved.filters)
        total = int(self.session.scalar(count_stmt) or 0)

        page = resolved.page or PageSpec()
        stmt = stmt.offset(page.offset).limit(page.limit)
        items = list(self.session.scalars(stmt).all())
        return PageResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def bulk_create(self, payloads: list[dict[str, Any]]) -> list[ModelT]:
        items = [self.model(**payload) for payload in payloads]
        self.session.add_all(items)
        self._flush()
        return items

    def bulk_update(self, records: list[tuple[Any, dict[str, Any]]]) -> list[ModelT]:
        out: list[ModelT] = []
        for record_id, payload in records:
            out.append(self.update(record_id, payload))
        self._flush()
        return out

    def bulk_delete(self, record_ids: list[Any]) -> int:
        deleted = 0
        for record_id in record_ids:
            self.delete(record_id)
            deleted += 1
        self._flush()
        return deleted

    def _apply_filters(self, stmt: Select[Any], filters: list[FilterSpec]) -> Select[Any]:
        for filter_spec in filters:
            column = getattr(self.model, filter_spec.field)
            op = filter_spec.operator
            value = filter_spec.value

            if op == FilterOperator.EQ:
                stmt = stmt.where(column == value)
            elif op == FilterOperator.NE:
                stmt = stmt.where(column != value)
            elif op == FilterOperator.GT:
                stmt = stmt.where(column > value)
            elif op == FilterOperator.GTE:
                stmt = stmt.where(column >= value)
            elif op == FilterOperator.LT:
                stmt = stmt.where(column < value)
            elif op == FilterOperator.LTE:
                stmt = stmt.where(column <= value)
            elif op == FilterOperator.IN:
                stmt = stmt.where(column.in_(value))
            elif op == FilterOperator.LIKE:
                stmt = stmt.where(column.like(value))
            elif op == FilterOperator.ILIKE:
                stmt = stmt.where(column.ilike(value))
            elif op == FilterOperator.IS_NULL:
                stmt = stmt.where(column.is_(None if value else value))
        return stmt

    def _apply_sorts(self, stmt: Select[Any], sorts: list[SortSpec]) -> Select[Any]:
        for sort in sorts:
            column = getattr(self.model, sort.field)
            stmt = stmt.order_by(column.desc() if sort.descending else column.asc())
        return stmt

    def _flush(self) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(str(exc)) from exc
        except SQLAlchemyError as exc:
            raise DBError(str(exc)) from exc


class UnitOfWork(AbstractContextManager["UnitOfWork"]):
    """Transaction helper wrapping a session with explicit commit/rollback."""

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "UnitOfWork":
        self.session = self._session_factory()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self.session is None:
            return False
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        except SQLAlchemyError as err:
            raise TransactionError(str(err)) from err
        finally:
            self.session.close()
            self.session = None
        return False

    def repository(self, model: type[ModelT]) -> Repository[ModelT]:
        if self.session is None:
            raise TransactionError("UnitOfWork session is not active")
        return Repository(model=model, session=self.session)
