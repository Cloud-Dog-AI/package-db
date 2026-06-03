"""Canonical SQL Core facade (FR.SQL.FACADE.1).

cloud_dog_db owns the platform's relational stack (SQLAlchemy + the DB-API drivers
``psycopg``/``PyMySQL``). Services consume SQL **only** through cloud_dog_db: the
engine/session/repository APIs for typed access, and this module for the SQLAlchemy
Core constructs needed to build ad-hoc tables, reflections and queries — so a
consuming service imports neither ``sqlalchemy`` nor a SQL driver directly.

    from cloud_dog_db.sql import MetaData, Table, Column, String, select, Engine

The drivers are pulled by the ``[sql]`` extra; importing this module needs only
SQLAlchemy (a core dependency).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    and_,
    asc,
    create_engine,
    delete,
    desc,
    func,
    insert,
    inspect,
    not_,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchTableError, SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

#: SQLAlchemy's ``update`` is also exported under the ``sql_update`` alias some
#: call sites use to avoid shadowing a local ``update`` argument.
sql_update = update

__all__ = [
    "JSON",
    "Boolean",
    "Column",
    "Date",
    "DateTime",
    "Float",
    "Integer",
    "LargeBinary",
    "MetaData",
    "Numeric",
    "String",
    "Table",
    "Text",
    "and_",
    "asc",
    "create_engine",
    "delete",
    "desc",
    "func",
    "insert",
    "inspect",
    "not_",
    "or_",
    "select",
    "text",
    "update",
    "sql_update",
    "Engine",
    "NoSuchTableError",
    "SQLAlchemyError",
    "ColumnElement",
]
