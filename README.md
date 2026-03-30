# cloud_dog_db

**Part of the [Cloud-Dog AI Platform](https://www.cloud-dog.ai)**

> Intelligent automation through composable AI agents, MCP servers, and shared platform services.

## About Cloud-Dog AI

Cloud-Dog AI is a platform of 10+ composable services for AI-powered business automation — natural language SQL queries, email management, file operations, git workflows, notification delivery, and expert knowledge retrieval. All services share a common set of platform packages for configuration, logging, authentication, job queues, LLM integration, vector databases, and caching.

This package provides: PS-DB platform database foundation package for Cloud-Dog services

## Installation

```bash
pip install cloud_dog_db
```

Available from the [Cloud-Dog AI package registry](https://www.cloud-dog.ai/packages).

## Quick Start

```python
from cloud_dog_db import *

# See API Reference below for available functions and classes
```

## API Reference

### Exports

```python
from cloud_dog_db import (
    AsyncSessionManager,
    AuditMixin,
    ConflictError,
    DBError,
    DatabaseSettings,
    FilterOperator,
    FilterSpec,
    MigrationRunner,
    PageResult,
    PageSpec,
    PlatformBase,
    QuerySpec,
    RecordNotFoundError,
    Repository,
    SoftDeleteMixin,
    SortSpec,
    SyncSessionManager,
    TenantMixin,
    TimestampMixin,
    TransactionError,
    UnitOfWork,
    build_async_engine,
    build_sync_engine,
    check_migration_revision,
    naming_convention,
    probe_database,
    require_revision,
)
```

## Dependencies

- `SQLAlchemy>=2.0,<3.0`
- `alembic>=1.13,<2.0`
- `pydantic>=2.7,<3.0`

## Testing

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run unit tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=cloud_dog_db --cov-report=term-missing
```

### Test Structure
- `tests/unit/` — Unit tests (no external dependencies)
- `tests/integration/` — Integration tests (requires running services)

## Related Packages

| Package | Description |
|---------|-------------|
| cloud-dog-config | Layered configuration with Vault integration |
| cloud-dog-logging | Structured JSON logging with correlation IDs |
| cloud-dog-api-kit | FastAPI toolkit with middleware and routing |
| cloud-dog-idam | Identity and access management client |
| cloud-dog-jobs | Background job scheduling and execution |
| cloud-dog-llm | LLM client abstraction (OpenAI, Ollama, etc.) |
| cloud-dog-vdb | Vector database client (Infinity, pgvector) |
| cloud-dog-cache | Caching abstraction with Redis/Valkey support |
| cloud-dog-tokens | Design tokens for UI consistency |
| cloud-dog-ui | React component library |
| cloud-dog-shell | Application shell and navigation |
| cloud-dog-auth | Frontend authentication flows |
| cloud-dog-api-client | TypeScript API client |
| cloud-dog-config-fe | Frontend configuration management |
| cloud-dog-testing | Test utilities and fixtures |

## Version

0.1.0

---

## Licence

Apache 2.0

Copyright 2026 [Cloud-Dog](https://www.cloud-dog.ai), Viewdeck Engineering Limited ([viewdeck.io](https://www.viewdeck.io))

[info@cloud-dog.ai](mailto:info@cloud-dog.ai)
