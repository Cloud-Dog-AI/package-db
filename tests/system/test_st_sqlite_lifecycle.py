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
from sqlalchemy import Integer, String, create_engine
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


def test_sqlite_file_db_migrate_and_crud(tmp_path: Path, db_settings: DatabaseSettings) -> None:
    if db_settings.dialect.value != "sqlite":
        pytest.skip("ST sqlite lifecycle only runs for sqlite env")

    sqlite_path = Path(db_settings.path or db_settings.database or tmp_path / "st.db")
    if not sqlite_path.is_absolute():
        sqlite_path = (Path.cwd() / sqlite_path).resolve()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if sqlite_path.exists():
        sqlite_path.unlink()

    url = f"sqlite+pysqlite:///{sqlite_path}"
    alembic_dir = _prepare_alembic_dir(tmp_path)
    runner = MigrationRunner(MigrationConfig(script_location=str(alembic_dir), sqlalchemy_url=url))
    runner.upgrade("head")

    engine = create_engine(url)
    with Session(engine) as session:
        repo = Repository(Widget, session)
        created = repo.create({"name": "st-widget", "status": "new"})
        session.commit()
        fetched = repo.get(created.id)
        assert fetched.name == "st-widget"
