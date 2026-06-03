"""Document repositories for MongoDB and CouchDB (FR.NS.1).

Driver imports (``pymongo``, ``couchdb``) are confined to this module per the
W28E-500 forbidden-pattern rule and loaded lazily so the optional extras are only
needed for the backend actually used.
"""

from __future__ import annotations

import re
from typing import Any

from cloud_dog_db.config.models import DatabaseDialect
from cloud_dog_db.crud.repository import ConflictError, DBError, RecordNotFoundError
from cloud_dog_db.crud.specs import FilterOperator, PageResult, PageSpec, QuerySpec
from cloud_dog_db.nosql._filters import to_mongo_filter, to_mongo_sort
from cloud_dog_db.nosql.settings import NoSqlSettings


def _norm_mongo(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    return out


class MongoDocumentRepository:
    """MongoDB-backed :class:`~cloud_dog_db.nosql.protocols.DocumentRepository`."""

    def __init__(self, settings: NoSqlSettings, database: str, collection: str, *, client: Any = None):
        if settings.dialect != DatabaseDialect.MONGODB:
            raise DBError(f"MongoDocumentRepository requires MONGODB dialect, got {settings.dialect}")
        from pymongo import MongoClient  # lazy — extra [mongodb]

        self._owns_client = client is None
        self._client = client or MongoClient(
            host=settings.host,
            port=settings.port,
            username=settings.username,
            password=settings.password_plain(),
            serverSelectionTimeoutMS=settings.timeout_seconds * 1000,
            tls=settings.use_ssl,
            tlsAllowInvalidCertificates=not settings.verify_certs,
        )
        self._collection = self._client[database or settings.database or "cloud_dog"][collection]

    def create(self, document: dict[str, Any]) -> dict[str, Any]:
        from pymongo.errors import DuplicateKeyError

        payload = dict(document)
        if "id" in payload:
            payload["_id"] = payload.pop("id")
        try:
            result = self._collection.insert_one(payload)
        except DuplicateKeyError as exc:  # pragma: no cover - backend specific
            raise ConflictError(str(exc)) from exc
        payload["_id"] = result.inserted_id
        return _norm_mongo(payload)  # type: ignore[return-value]

    def get(self, record_id: Any) -> dict[str, Any]:
        doc = self._collection.find_one({"_id": record_id})
        if doc is None:
            raise RecordNotFoundError(f"document({record_id}) not found")
        return _norm_mongo(doc)  # type: ignore[return-value]

    def update(self, record_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
        patch = {k: v for k, v in payload.items() if k not in {"id", "_id"}}
        result = self._collection.update_one({"_id": record_id}, {"$set": patch})
        if result.matched_count == 0:
            raise RecordNotFoundError(f"document({record_id}) not found")
        return self.get(record_id)

    def delete(self, record_id: Any) -> None:
        result = self._collection.delete_one({"_id": record_id})
        if result.deleted_count == 0:
            raise RecordNotFoundError(f"document({record_id}) not found")

    def list(self, spec: QuerySpec | None = None) -> PageResult[dict[str, Any]]:
        query = to_mongo_filter(spec)
        page = (spec.page if spec else None) or PageSpec()
        total = int(self._collection.count_documents(query))
        cursor = self._collection.find(query)
        sort = to_mongo_sort(spec)
        if sort:
            cursor = cursor.sort(sort)
        cursor = cursor.skip(page.offset).limit(page.limit)
        items = [_norm_mongo(d) for d in cursor]
        return PageResult(items=items, total=total, limit=page.limit, offset=page.offset)  # type: ignore[arg-type]

    def count(self, spec: QuerySpec | None = None) -> int:
        return int(self._collection.count_documents(to_mongo_filter(spec)))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


# ── CouchDB ────────────────────────────────────────────────────────────────


def _matches(doc: dict[str, Any], spec: QuerySpec | None) -> bool:
    if not spec:
        return True
    for f in spec.filters:
        value = doc.get(f.field)
        op = f.operator
        if op == FilterOperator.EQ and not value == f.value:
            return False
        if op == FilterOperator.NE and not value != f.value:
            return False
        if op == FilterOperator.GT and not (value is not None and value > f.value):
            return False
        if op == FilterOperator.GTE and not (value is not None and value >= f.value):
            return False
        if op == FilterOperator.LT and not (value is not None and value < f.value):
            return False
        if op == FilterOperator.LTE and not (value is not None and value <= f.value):
            return False
        if op == FilterOperator.IN and value not in f.value:
            return False
        if op == FilterOperator.LIKE and not re.match(_like(f.value), str(value)):
            return False
        if op == FilterOperator.ILIKE and not re.match(_like(f.value), str(value), re.IGNORECASE):
            return False
        if op == FilterOperator.IS_NULL and ((value is None) != bool(f.value)):
            return False
    return True


def _like(value: str) -> str:
    return "^" + re.escape(str(value)).replace("\\%", ".*").replace("\\_", ".") + "$"


def _norm_couch(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    if "_id" in out:
        out["id"] = out.pop("_id")
    return out


class CouchDocumentRepository:
    """CouchDB-backed :class:`~cloud_dog_db.nosql.protocols.DocumentRepository`."""

    def __init__(self, settings: NoSqlSettings, database: str, *, server: Any = None):
        if settings.dialect != DatabaseDialect.COUCHDB:
            raise DBError(f"CouchDocumentRepository requires COUCHDB dialect, got {settings.dialect}")
        import couchdb  # lazy — extra [couchdb]

        if server is None:
            auth = ""
            if settings.username:
                auth = f"{settings.username}:{settings.password_plain()}@"
            url = f"{settings.http_scheme()}://{auth}{settings.host}:{settings.port}/"
            server = couchdb.Server(url)
        self._server = server
        name = database or settings.database or "cloud_dog"
        self._db = server[name] if name in server else server.create(name)

    def create(self, document: dict[str, Any]) -> dict[str, Any]:
        import couchdb

        payload = dict(document)
        if "id" in payload:
            payload["_id"] = str(payload.pop("id"))
        try:
            doc_id, _rev = self._db.save(payload)
        except couchdb.http.ResourceConflict as exc:
            raise ConflictError(str(exc)) from exc
        return _norm_couch(dict(self._db[doc_id]))

    def get(self, record_id: Any) -> dict[str, Any]:
        import couchdb

        try:
            doc = self._db[str(record_id)]
        except (KeyError, couchdb.http.ResourceNotFound) as exc:
            raise RecordNotFoundError(f"document({record_id}) not found") from exc
        return _norm_couch(dict(doc))

    def update(self, record_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
        import couchdb

        try:
            doc = self._db[str(record_id)]
        except (KeyError, couchdb.http.ResourceNotFound) as exc:
            raise RecordNotFoundError(f"document({record_id}) not found") from exc
        for key, value in payload.items():
            if key not in {"id", "_id", "_rev"}:
                doc[key] = value
        self._db.save(doc)
        return _norm_couch(dict(self._db[str(record_id)]))

    def delete(self, record_id: Any) -> None:
        import couchdb

        try:
            doc = self._db[str(record_id)]
        except (KeyError, couchdb.http.ResourceNotFound) as exc:
            raise RecordNotFoundError(f"document({record_id}) not found") from exc
        self._db.delete(doc)

    def list(self, spec: QuerySpec | None = None) -> PageResult[dict[str, Any]]:
        page = (spec.page if spec else None) or PageSpec()
        rows = [
            _norm_couch(dict(row.doc))
            for row in self._db.view("_all_docs", include_docs=True)
            if row.doc is not None and not str(row.id).startswith("_design")
        ]
        filtered = [d for d in rows if _matches(d, spec)]
        for s in reversed(spec.sorts if spec else []):
            filtered.sort(key=lambda d: (d.get(s.field) is None, d.get(s.field)), reverse=s.descending)
        total = len(filtered)
        window = filtered[page.offset : page.offset + page.limit]
        return PageResult(items=window, total=total, limit=page.limit, offset=page.offset)

    def count(self, spec: QuerySpec | None = None) -> int:
        return self.list(QuerySpec(filters=spec.filters if spec else [], page=PageSpec(limit=10**9))).total

    def close(self) -> None:  # couchdb client is stateless HTTP
        return None


def build_document_client(settings: NoSqlSettings, *, database: str = "", collection: str = "documents") -> Any:
    """Factory dispatch for document repositories (FR.NS.1)."""
    if settings.dialect == DatabaseDialect.MONGODB:
        return MongoDocumentRepository(settings, database=database, collection=collection)
    if settings.dialect == DatabaseDialect.COUCHDB:
        return CouchDocumentRepository(settings, database=database or collection)
    raise DBError(f"{settings.dialect} is not a document dialect")
