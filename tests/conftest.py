# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared pytest config, mandatory --env loading, and Vault placeholder resolution."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cloud_dog_db.config.models import DatabaseSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_VAULT_VARS = ("VAULT_ADDR", "VAULT_TOKEN", "VAULT_MOUNT_POINT", "VAULT_CONFIG_PATH")
_PLACEHOLDER_RE = re.compile(r"^\$\{\s*vault\.([a-zA-Z0-9_.-]+)\s*\}$")


def pytest_addoption(parser: pytest.Parser) -> None:
    # `--env` may already be provided by another workspace plugin.
    try:
        parser.addoption(
            "--env",
            action="append",
            default=None,
            help="Required env file path(s). Can be repeated or comma-separated.",
        )
    except ValueError:
        return


def _normalise_env_args(raw: list[str] | None) -> list[Path]:
    out: list[Path] = []
    for value in raw or []:
        for part in value.split(","):
            p = part.strip()
            if not p:
                continue
            path = Path(p)
            if not path.is_absolute():
                path = (PROJECT_ROOT / p).resolve()
            out.append(path)
    return out


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise pytest.UsageError(f"Invalid env line in {path}: {line}")
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _vault_payload() -> dict[str, Any] | None:
    if any(not os.environ.get(k) for k in REQUIRED_VAULT_VARS):
        return None
    url = (
        f"{os.environ['VAULT_ADDR'].rstrip('/')}/v1/"
        f"{os.environ['VAULT_MOUNT_POINT']}/data/{os.environ['VAULT_CONFIG_PATH']}"
    )
    cmd = ["curl", "-sS", "-H", f"X-Vault-Token: {os.environ['VAULT_TOKEN']}", url]
    raw = subprocess.check_output(cmd, text=True)
    parsed = json.loads(raw)
    data = parsed.get("data", {}).get("data", {})
    if "json" in data and isinstance(data["json"], dict):
        return data["json"]
    if "content" in data and isinstance(data["content"], str):
        try:
            decoded = json.loads(data["content"])
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _resolve_vault_path(payload: dict[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def _resolve_value(raw: str, payload: dict[str, Any] | None) -> str:
    match = _PLACEHOLDER_RE.match(raw)
    if not match:
        return raw
    if payload is None:
        raise pytest.UsageError(
            f"Vault placeholder {raw} found but Vault env contract is missing; "
            "source /opt/iac/Development/cloud-dog-ai/env-vault first"
        )
    resolved = _resolve_vault_path(payload, match.group(1))
    if resolved is None:
        return ""
    return str(resolved)


def pytest_configure(config: pytest.Config) -> None:
    env_files = _normalise_env_args(config.getoption("env"))
    if not env_files:
        raise pytest.UsageError("Missing required --env <file> argument")

    payload: dict[str, Any] | None = None
    for env_file in env_files:
        if not env_file.exists():
            raise pytest.UsageError(f"Env file not found: {env_file}")
        raw = _load_env_file(env_file)
        if any(_PLACEHOLDER_RE.match(v) for v in raw.values()):
            payload = payload or _vault_payload()
        for key, value in raw.items():
            os.environ[key] = _resolve_value(value, payload)


@pytest.fixture(scope="session")
def db_settings() -> DatabaseSettings:
    return DatabaseSettings.from_env(prefix="CLOUD_DOG_DB__")


@pytest.fixture(scope="session")
def vault_providers() -> dict[str, Any]:
    """Resolved ``dev.databases.providers`` mapping for NoSQL ST/IT tests.

    Fails (not skips) if Vault is not reachable per RULES §5.10/§5.6.
    """
    payload = _vault_payload()
    if payload is None:
        pytest.fail(
            "Vault providers unavailable — source /opt/iac/Development/cloud-dog-ai/env-vault "
            "before running NoSQL ST/IT tests (RULES §3.2 / §5.6)."
        )
    try:
        return _resolve_vault_path(payload, "dev.databases.providers")
    except KeyError:
        pytest.fail("dev.databases.providers not found in Vault config")


@pytest.fixture(scope="session")
def pgvector_settings():
    """Local pgvector container for the FR.NS.5 PGVECTOR ST row.

    The shared Postgres (``dev.databases.providers.postgres`` on db2) does not ship the
    ``vector`` extension control file, so a local pgvector container is used per the
    W28E-605 "real local backend containers where practical" allowance. The container is
    created and removed by this fixture (PC32 — no leftover containers).
    """
    import secrets
    import socket
    import time

    from cloud_dog_db.nosql.settings import NoSqlSettings

    # Use --network host (published ports are not host-reachable in this fabric; the
    # platform local-container pattern binds directly on the host network).
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    name = "w28e605-pgvector-st"
    password = secrets.token_hex(8)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    run = subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", "host",
         "-e", f"POSTGRES_PASSWORD={password}", "-e", "POSTGRES_DB=postgres",
         "pgvector/pgvector:pg16", "-c", f"port={port}"],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        pytest.fail(f"could not start local pgvector container: {run.stderr.strip()[:200]}")
    try:
        settings = NoSqlSettings(
            dialect="pgvector", host="127.0.0.1", port=port,
            username="postgres", password=password, database="postgres",
        )
        import psycopg

        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with psycopg.connect(host="127.0.0.1", port=port, user="postgres",
                                     password=password, dbname="postgres", connect_timeout=3):
                    break
            except Exception:
                time.sleep(1.5)
        else:
            pytest.fail("local pgvector container did not become ready within 60s")
        yield settings
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture(scope="session")
def local_elasticsearch_settings():
    """Local Elasticsearch container for the FR.NS.2 Elasticsearch ST row.

    The shared ``dev.databases.providers.elasticsearch`` cluster (elastic0) has a stuck
    master (pending cluster tasks queued >22 days — disk-threshold blocked) so no index
    can be created there. A local ES container is used per the W28E-605 local-container
    allowance, created and removed by this fixture (PC32).
    """
    import socket
    import time
    import urllib.request

    from cloud_dog_db.nosql.settings import NoSqlSettings

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    name = "w28e605-es-st"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    run = subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", "host",
         "-e", "discovery.type=single-node", "-e", "xpack.security.enabled=false",
         "-e", "ES_JAVA_OPTS=-Xms512m -Xmx512m", "-e", f"http.port={port}",
         "docker.elastic.co/elasticsearch/elasticsearch:8.17.0"],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        pytest.fail(f"could not start local elasticsearch container: {run.stderr.strip()[:200]}")
    try:
        settings = NoSqlSettings(dialect="elasticsearch", host="127.0.0.1", port=port)
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=3) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(3)
        else:
            pytest.fail("local elasticsearch container did not become ready within 120s")
        yield settings
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
