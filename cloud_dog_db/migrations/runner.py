"""Alembic migration runner APIs and CLI entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.util import CommandError


class MigrationCommandError(RuntimeError):
    """Raised when migration execution fails."""


@dataclass(slots=True)
class MigrationConfig:
    script_location: str
    sqlalchemy_url: str
    version_table: str = "alembic_version"
    version_table_schema: str | None = None
    version_locations: str | None = None


def build_alembic_config(cfg: MigrationConfig) -> Config:
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", cfg.script_location)
    alembic_cfg.set_main_option("sqlalchemy.url", cfg.sqlalchemy_url)
    alembic_cfg.set_main_option("version_table", cfg.version_table)
    if cfg.version_table_schema:
        alembic_cfg.set_main_option("version_table_schema", cfg.version_table_schema)
    if cfg.version_locations:
        alembic_cfg.set_main_option("version_locations", cfg.version_locations)
    return alembic_cfg


class MigrationRunner:
    """Wrap Alembic command API with explicit errors and convenience helpers."""

    def __init__(self, config: MigrationConfig):
        self.config = config

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        try:
            fn(build_alembic_config(self.config), *args, **kwargs)
        except CommandError as exc:
            raise MigrationCommandError(str(exc)) from exc

    def current(self, verbose: bool = False) -> None:
        self._call(command.current, verbose=verbose)

    def upgrade(self, revision: str = "head") -> None:
        self._call(command.upgrade, revision)

    def downgrade(self, revision: str) -> None:
        self._call(command.downgrade, revision)

    def stamp(self, revision: str) -> None:
        self._call(command.stamp, revision)

    def init(self, directory: str, template: str = "generic", package: bool = False) -> None:
        try:
            command.init(build_alembic_config(self.config), directory, template=template, package=package)
        except CommandError as exc:
            raise MigrationCommandError(str(exc)) from exc

    def bootstrap(self, baseline_revision: str = "base", target_revision: str = "head") -> None:
        """Initialise empty DB state and migrate to target revision."""

        self.stamp(baseline_revision)
        self.upgrade(target_revision)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="cloud_dog_db migration runner")
    parser.add_argument("command", choices=["current", "upgrade", "downgrade", "stamp", "init", "bootstrap"])
    parser.add_argument("revision", nargs="?", default="head")
    parser.add_argument("--script-location", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--version-table", default="alembic_version")
    parser.add_argument("--version-table-schema", default=None)
    parser.add_argument("--version-locations", default=None)
    parser.add_argument("--init-dir", default="migrations")
    parser.add_argument("--template", default="generic")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = MigrationConfig(
        script_location=args.script_location,
        sqlalchemy_url=args.url,
        version_table=args.version_table,
        version_table_schema=args.version_table_schema,
        version_locations=args.version_locations,
    )
    runner = MigrationRunner(cfg)

    if args.command == "current":
        runner.current(verbose=True)
    elif args.command == "upgrade":
        runner.upgrade(args.revision)
    elif args.command == "downgrade":
        runner.downgrade(args.revision)
    elif args.command == "stamp":
        runner.stamp(args.revision)
    elif args.command == "init":
        Path(args.init_dir).mkdir(parents=True, exist_ok=True)
        runner.init(args.init_dir, template=args.template)
    elif args.command == "bootstrap":
        runner.bootstrap(target_revision=args.revision)
