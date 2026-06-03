"""Search repositories for Elasticsearch and OpenSearch (FR.NS.2).

Driver imports (``elasticsearch``, ``opensearchpy``) are confined to this module and
loaded lazily. Both repositories share one base so the call contract is identical.
"""

from __future__ import annotations

from typing import Any

from cloud_dog_db.config.models import DatabaseDialect
from cloud_dog_db.crud.repository import DBError, RecordNotFoundError
from cloud_dog_db.crud.specs import PageResult, PageSpec, QuerySpec
from cloud_dog_db.nosql._filters import to_es_query, to_es_sort
from cloud_dog_db.nosql.settings import NoSqlSettings


class _SearchRepositoryBase:
    """Shared Elasticsearch/OpenSearch repository logic."""

    def __init__(self, client: Any, index: str):
        self._client = client
        self._index = index

    # backend-specific raw calls
    def _raw_index(self, record_id: Any, document: dict[str, Any]) -> None: ...
    def _raw_get(self, record_id: Any) -> dict[str, Any] | None: ...
    def _raw_delete(self, record_id: Any) -> bool: ...
    def _raw_search(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def _raw_refresh(self) -> None: ...
    def _ensure_index(self) -> None: ...

    def index(self, record_id: Any, document: dict[str, Any]) -> dict[str, Any]:
        self._ensure_index()
        body = {k: v for k, v in document.items() if k != "id"}
        self._raw_index(record_id, body)
        return {"id": str(record_id), **body}

    def get(self, record_id: Any) -> dict[str, Any]:
        source = self._raw_get(record_id)
        if source is None:
            raise RecordNotFoundError(f"document({record_id}) not found")
        return {"id": str(record_id), **source}

    def delete(self, record_id: Any) -> None:
        if not self._raw_delete(record_id):
            raise RecordNotFoundError(f"document({record_id}) not found")

    def search(self, query: dict[str, Any] | str, spec: QuerySpec | None = None) -> PageResult[dict[str, Any]]:
        page = (spec.page if spec else None) or PageSpec()
        if isinstance(query, str):
            query = {"query_string": {"query": query}}
        body: dict[str, Any] = {"query": query, "from": page.offset, "size": page.limit}
        sort = to_es_sort(spec)
        if sort:
            body["sort"] = sort
        resp = self._raw_search(body)
        hits = resp.get("hits", {})
        total_obj = hits.get("total", 0)
        total = int(total_obj.get("value", 0)) if isinstance(total_obj, dict) else int(total_obj)
        items = [{"id": h.get("_id"), **(h.get("_source") or {})} for h in hits.get("hits", [])]
        return PageResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def list(self, spec: QuerySpec | None = None) -> PageResult[dict[str, Any]]:
        return self.search(to_es_query(spec), spec)

    def refresh(self) -> None:
        self._raw_refresh()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - client may be stateless
            pass


class ElasticsearchSearchRepository(_SearchRepositoryBase):
    """Elasticsearch-backed search repository."""

    def __init__(self, settings: NoSqlSettings, index: str, *, client: Any = None):
        if settings.dialect != DatabaseDialect.ELASTICSEARCH:
            raise DBError(f"ElasticsearchSearchRepository requires ELASTICSEARCH dialect, got {settings.dialect}")
        from elasticsearch import Elasticsearch  # lazy — extra [elasticsearch]

        if client is None:
            client = Elasticsearch(
                hosts=[settings.base_http_url()],
                basic_auth=(settings.username, settings.password_plain()) if settings.username else None,
                verify_certs=settings.verify_certs,
                ssl_show_warn=False,
                request_timeout=max(30, settings.timeout_seconds),
            )
        super().__init__(client, index)

    def _ensure_index(self) -> None:
        import time

        from elasticsearch import ApiError, BadRequestError

        if self._client.indices.exists(index=self._index):
            return
        last_exc: Exception | None = None
        for _attempt in range(3):
            try:
                self._client.indices.create(
                    index=self._index,
                    settings={"number_of_replicas": 0},
                    master_timeout="120s",
                    timeout="120s",
                )
                self._client.cluster.health(index=self._index, wait_for_status="yellow", timeout="60s")
                return
            except BadRequestError:
                return  # already exists (race)
            except ApiError as exc:  # 503 process_cluster_event_timeout on a busy single master
                last_exc = exc
                if self._client.indices.exists(index=self._index):
                    return
                time.sleep(3)
        if last_exc is not None:
            raise last_exc

    def _raw_index(self, record_id: Any, document: dict[str, Any]) -> None:
        self._client.index(index=self._index, id=str(record_id), document=document, refresh=True)

    def _raw_get(self, record_id: Any) -> dict[str, Any] | None:
        from elasticsearch import NotFoundError

        try:
            resp = self._client.get(index=self._index, id=str(record_id))
        except NotFoundError:
            return None
        return resp.get("_source")

    def _raw_delete(self, record_id: Any) -> bool:
        from elasticsearch import NotFoundError

        try:
            self._client.delete(index=self._index, id=str(record_id), refresh=True)
        except NotFoundError:
            return False
        return True

    def _raw_search(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._client.search(index=self._index, **body)

    def _raw_refresh(self) -> None:
        self._client.indices.refresh(index=self._index)


class OpenSearchSearchRepository(_SearchRepositoryBase):
    """OpenSearch-backed search repository."""

    def __init__(self, settings: NoSqlSettings, index: str, *, client: Any = None):
        if settings.dialect != DatabaseDialect.OPENSEARCH:
            raise DBError(f"OpenSearchSearchRepository requires OPENSEARCH dialect, got {settings.dialect}")
        from opensearchpy import OpenSearch  # lazy — extra [opensearch]

        if client is None:
            client = OpenSearch(
                hosts=[{"host": settings.host, "port": settings.port}],
                http_auth=(settings.username, settings.password_plain()) if settings.username else None,
                use_ssl=settings.use_ssl,
                verify_certs=settings.verify_certs,
                ssl_show_warn=False,
                timeout=settings.timeout_seconds,
            )
        super().__init__(client, index)

    def _ensure_index(self) -> None:
        if not self._client.indices.exists(index=self._index):
            self._client.indices.create(index=self._index)

    def _raw_index(self, record_id: Any, document: dict[str, Any]) -> None:
        self._client.index(index=self._index, id=str(record_id), body=document, refresh=True)

    def _raw_get(self, record_id: Any) -> dict[str, Any] | None:
        from opensearchpy.exceptions import NotFoundError

        try:
            resp = self._client.get(index=self._index, id=str(record_id))
        except NotFoundError:
            return None
        return resp.get("_source")

    def _raw_delete(self, record_id: Any) -> bool:
        from opensearchpy.exceptions import NotFoundError

        try:
            self._client.delete(index=self._index, id=str(record_id), refresh=True)
        except NotFoundError:
            return False
        return True

    def _raw_search(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._client.search(index=self._index, body=body)

    def _raw_refresh(self) -> None:
        self._client.indices.refresh(index=self._index)


def build_search_client(settings: NoSqlSettings, *, index: str = "documents") -> Any:
    """Factory dispatch for search repositories (FR.NS.2)."""
    if settings.dialect == DatabaseDialect.ELASTICSEARCH:
        return ElasticsearchSearchRepository(settings, index=index)
    if settings.dialect == DatabaseDialect.OPENSEARCH:
        return OpenSearchSearchRepository(settings, index=index)
    raise DBError(f"{settings.dialect} is not a search dialect")
