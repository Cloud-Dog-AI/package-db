# platform-db

**Package:** `cloud_dog_db`  
**Standard track:** Platform DB package foundation (W15A)

`cloud_dog_db` provides reusable SQL database foundations for Cloud-Dog services with support for SQLite, MariaDB/MySQL, and PostgreSQL.

## Capabilities

- Dialect-aware sync/async SQLAlchemy engine/session factories
- Alembic migration runner API + CLI (`init`, `current`, `upgrade`, `downgrade`, `stamp`)
- Schema bootstrap helper (baseline -> head)
- Generic CRUD repository with filter/sort/pagination/bulk helpers
- Transactional unit-of-work helper
- DB health/readiness probes including migration revision checks

## Quick Start

```python
from cloud_dog_db.config.models import DatabaseSettings
from cloud_dog_db.engine.factory import build_sync_engine
from cloud_dog_db.session.session_manager import SyncSessionManager

settings = DatabaseSettings.from_env(prefix="CLOUD_DOG_DB__")
engine = build_sync_engine(settings)
manager = SyncSessionManager(engine)

with manager.session() as session:
    session.execute("SELECT 1")
```

## CLI

```bash
cloud-dog-db-migrate --script-location ./database/migrations --url sqlite:///./app.db upgrade head
```

## Documents

- `REQUIREMENTS.md`
- `ARCHITECTURE.md`
- `TESTS.md`

---

## Licence

Apache-2.0 — Copyright (c) 2026 Cloud-Dog, Viewdeck Engineering Limited
