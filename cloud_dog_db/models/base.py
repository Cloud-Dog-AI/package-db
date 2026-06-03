"""Declarative base with standardised naming convention for all Cloud-Dog ORM models."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

naming_convention: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class PlatformBase(DeclarativeBase):
    """Shared declarative base for all Cloud-Dog ORM models.

    Provides a ``MetaData`` instance with the platform naming convention so that
    all generated constraints (indexes, unique, foreign-key, check, primary-key)
    follow a deterministic, cross-dialect-safe naming scheme.

    Projects import this base and subclass it for their models::

        from cloud_dog_db.models import PlatformBase

        class MyModel(PlatformBase):
            __tablename__ = "ns_my_models"
            ...
    """

    metadata = MetaData(naming_convention=naming_convention)
