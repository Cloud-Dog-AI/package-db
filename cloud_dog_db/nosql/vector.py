"""pgvector helper / probe for Postgres (FR.NS.7, FR.NS.5 PGVECTOR).

Uses ``psycopg`` (already a SQL dependency). The ``pgvector`` extra adds the
``pgvector`` Python adapter for typed vector binding.
"""

from __future__ import annotations

from typing import Any

from cloud_dog_db.crud.repository import DBError, RecordNotFoundError
from cloud_dog_db.nosql.settings import NoSqlSettings


def _connect(settings: NoSqlSettings) -> Any:
    import psycopg  # lazy

    return psycopg.connect(
        host=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password_plain(),
        dbname=settings.database or "postgres",
        connect_timeout=settings.timeout_seconds,
    )


def probe_pgvector(settings: NoSqlSettings) -> dict[str, Any]:
    """Report pgvector availability/installation on the target Postgres (FR.NS.7)."""
    with _connect(settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        ok = cur.fetchone()[0] == 1
        cur.execute("SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'vector'")
        row = cur.fetchone()
    available = row is not None
    installed = bool(row and row[1])
    return {
        "ok": ok,
        "extension_available": available,
        "extension_installed": installed,
        "default_version": row[0] if row else None,
    }


def ensure_vector_extension(settings: NoSqlSettings) -> None:
    """CREATE EXTENSION IF NOT EXISTS vector (requires privilege)."""
    with _connect(settings) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()


class PgVectorStore:
    """Minimal vector store: upsert embeddings and run nearest-neighbour search."""

    def __init__(self, settings: NoSqlSettings, table: str, dim: int, *, conn: Any = None):
        self._owns = conn is None
        self._conn = conn or _connect(settings)
        self._table = table
        self._dim = dim

    def create_table(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} "
                f"(id TEXT PRIMARY KEY, embedding vector({self._dim}), metadata JSONB)"
            )
            self._conn.commit()

    def upsert(self, record_id: str, embedding: list[float], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        import json

        vec = "[" + ",".join(str(float(x)) for x in embedding) + "]"
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._table} (id, embedding, metadata) VALUES (%s, %s, %s) "
                f"ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata",
                (record_id, vec, json.dumps(metadata or {})),
            )
            self._conn.commit()
        return {"id": record_id, "metadata": metadata or {}}

    def get(self, record_id: str) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT id, metadata FROM {self._table} WHERE id = %s", (record_id,))
            row = cur.fetchone()
        if row is None:
            raise RecordNotFoundError(f"vector({record_id}) not found")
        return {"id": row[0], "metadata": row[1]}

    def search(self, embedding: list[float], k: int = 5) -> list[dict[str, Any]]:
        vec = "[" + ",".join(str(float(x)) for x in embedding) + "]"
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT id, metadata, embedding <-> %s AS distance FROM {self._table} "
                f"ORDER BY embedding <-> %s LIMIT %s",
                (vec, vec, k),
            )
            rows = cur.fetchall()
        return [{"id": r[0], "metadata": r[1], "distance": float(r[2])} for r in rows]

    def close(self) -> None:
        if self._owns:
            self._conn.close()


def build_vector_client(settings: NoSqlSettings, *, table: str, dim: int) -> PgVectorStore:
    from cloud_dog_db.config.models import DatabaseDialect

    if settings.dialect not in {DatabaseDialect.PGVECTOR, DatabaseDialect.POSTGRESQL}:
        raise DBError(f"{settings.dialect} is not a pgvector dialect")
    return PgVectorStore(settings, table=table, dim=dim)
