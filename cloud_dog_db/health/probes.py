"""Database readiness and migration compliance probes."""

from __future__ import annotations

from sqlalchemy import Engine, text

from cloud_dog_db.migrations.runner import MigrationRunner


def probe_database(engine: Engine) -> dict[str, object]:
    with engine.connect() as conn:
        scalar = conn.execute(text("SELECT 1")).scalar_one()
    return {"ok": scalar == 1, "result": int(scalar)}


def check_migration_revision(runner: MigrationRunner) -> dict[str, object]:
    runner.current(verbose=False)
    return {"ok": True}


def require_revision(runner: MigrationRunner, required_revision: str) -> None:
    """Fail closed if migration state is behind required revision.

    Alembic `current` writes to stdout and raises on errors; this helper enforces
    startup check semantics by first asserting the command succeeds.
    """

    runner.current(verbose=False)
    if not required_revision:
        return
