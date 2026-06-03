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

from __future__ import annotations

import os

from cloud_dog_db.config.models import DatabaseDialect, DatabaseSettings


def test_from_env_parses_sqlite(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
    monkeypatch.setenv("CLOUD_DOG_DB__PATH", "./tmp/test.db")
    settings = DatabaseSettings.from_env()
    assert settings.dialect == DatabaseDialect.SQLITE
    assert settings.to_sync_url().startswith("sqlite+pysqlite:///")


def test_from_env_parses_network_dsn(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "postgresql")
    monkeypatch.setenv("CLOUD_DOG_DB__HOST", "db.local")
    monkeypatch.setenv("CLOUD_DOG_DB__PORT", "5432")
    monkeypatch.setenv("CLOUD_DOG_DB__USERNAME", "postgres")
    monkeypatch.setenv("CLOUD_DOG_DB__PASSWORD", "secret")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", "app")
    settings = DatabaseSettings.from_env()
    assert settings.to_sync_url().startswith("postgresql+psycopg://postgres:secret@db.local:5432/app")
    assert settings.to_async_url().startswith("postgresql+asyncpg://postgres:secret@db.local:5432/app")


def test_masked_url_hides_secret(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_DOG_DB__URL", "mysql+pymysql://root:supersecret@localhost:3306/demo")
    settings = DatabaseSettings.from_env()
    assert "supersecret" not in settings.masked_url()


# Defensively clear env vars mutated above when running as a whole suite.
for key in tuple(os.environ):
    if key.startswith("CLOUD_DOG_DB__") and key.endswith("__TEST_TEMP"):
        del os.environ[key]
