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

import shutil
from pathlib import Path

import pytest
from sqlalchemy import Integer, String, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from cloud_dog_db.config.models import DatabaseSettings
from cloud_dog_db.crud.repository import Repository
from cloud_dog_db.migrations.runner import MigrationConfig, MigrationRunner


class Base(DeclarativeBase):
    pass


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="new")


def _prepare_alembic_dir(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "assets" / "alembic"
    target = tmp_path / "alembic"
    shutil.copytree(source, target)
    return target


def _ensure_mysql_database(url: str) -> None:
    parsed = make_url(url)
    admin_url = parsed.set(database="mysql")
    engine = create_engine(admin_url)
    db_name = parsed.database
    with engine.begin() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}`"))


def test_it_mariadb_schema_init_and_rollback(tmp_path: Path, db_settings: DatabaseSettings) -> None:
    if db_settings.dialect.value != "mysql":
        pytest.skip("mariadb integration only in mysql env")

    url = db_settings.to_sync_url()
    _ensure_mysql_database(url)

    alembic_dir = _prepare_alembic_dir(tmp_path)
    runner = MigrationRunner(MigrationConfig(script_location=str(alembic_dir), sqlalchemy_url=url))
    runner.upgrade("head")

    engine = create_engine(url)
    with Session(engine) as session:
        repo = Repository(Widget, session)
        item = repo.create({"name": "it-mysql", "status": "new"})
        session.commit()
        assert repo.get(item.id).name == "it-mysql"

    runner.downgrade("base")
