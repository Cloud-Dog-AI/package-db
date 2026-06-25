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

from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from cloud_dog_db.config.models import DatabaseSettings
from cloud_dog_db.crud.repository import UnitOfWork
from cloud_dog_db.engine.factory import build_sync_engine
from cloud_dog_db.health.probes import probe_database
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


def test_application_consumer_style_flow(tmp_path: Path, db_settings: DatabaseSettings) -> None:
    db_settings = DatabaseSettings(dialect="sqlite", path=str(tmp_path / "at.db"))

    engine = build_sync_engine(db_settings)
    sync_url = str(engine.url)

    alembic_dir = _prepare_alembic_dir(tmp_path)
    runner = MigrationRunner(MigrationConfig(script_location=str(alembic_dir), sqlalchemy_url=sync_url))
    runner.upgrade("head")

    def session_factory() -> Session:
        return Session(create_engine(sync_url))

    with UnitOfWork(session_factory=session_factory) as uow:
        repo = uow.repository(Widget)
        created = repo.create({"name": "at-widget", "status": "new"})
        assert created.id is not None

    probe = probe_database(create_engine(sync_url))
    assert probe["ok"] is True
