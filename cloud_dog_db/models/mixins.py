"""Reusable ORM mixins for Cloud-Dog models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns (timezone-aware UTC)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds ``deleted_at`` and ``is_deleted`` for logical deletion."""

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )


class TenantMixin:
    """Adds ``tenant_id`` for multi-tenant isolation (future use)."""

    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )


class AuditMixin:
    """Adds ``created_by`` and ``updated_by`` actor tracking."""

    created_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    updated_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
