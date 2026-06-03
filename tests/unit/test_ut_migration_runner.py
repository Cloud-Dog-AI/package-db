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

import pytest
from alembic.util import CommandError

from cloud_dog_db.migrations.runner import MigrationCommandError, MigrationConfig, MigrationRunner, build_alembic_config


def test_build_alembic_config_sets_required_options() -> None:
    cfg = build_alembic_config(
        MigrationConfig(
            script_location="/tmp/alembic",
            sqlalchemy_url="sqlite:///tmp.db",
            version_table="my_version",
            version_table_schema="public",
        )
    )
    assert cfg.get_main_option("script_location") == "/tmp/alembic"
    assert cfg.get_main_option("sqlalchemy.url") == "sqlite:///tmp.db"
    assert cfg.get_main_option("version_table") == "my_version"
    assert cfg.get_main_option("version_table_schema") == "public"


def test_runner_maps_command_error(monkeypatch) -> None:
    runner = MigrationRunner(MigrationConfig(script_location="/tmp/alembic", sqlalchemy_url="sqlite:///tmp.db"))

    def boom(*_args, **_kwargs):
        raise CommandError("bad command")

    monkeypatch.setattr("cloud_dog_db.migrations.runner.command.upgrade", boom)
    with pytest.raises(MigrationCommandError):
        runner.upgrade("head")
