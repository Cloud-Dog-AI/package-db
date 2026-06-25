# Build Instructions

## Package
`cloud-dog-db` - database engine, sessions, and migrations.

## Prerequisites
- Python 3.11+
- pip

## Development Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip build twine
pip install -e ".[dev]"
```

If your environment resolves dependencies from an additional package index:
```bash
PYPI_URL=https://packages.example.com/simple/
pip install -e ".[dev]" --extra-index-url "$PYPI_URL"
```

## Local Use
Install the package in editable mode and import it from an interactive shell or another local project:
```bash
python -c "import cloud_dog_db; print('package import ok')"
```

## Run Tests
```bash
python -m pytest tests/unit --env tests/env-UT -v
```

## Build Distribution
```bash
python -m build
```

## Publish
```bash
twine upload --repository-url "$PYPI_URL" dist/*
```

## Dependencies
- `sqlalchemy`, `alembic`
- optional extras are declared in `pyproject.toml`

## Configuration
Tests and sample programs can read configuration from shell variables, a local env file, and package defaults where available.
