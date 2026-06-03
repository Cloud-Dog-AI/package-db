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

"""UT1.5 — PlatformBase, naming convention, and mixins."""

from __future__ import annotations

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cloud_dog_db.models.base import PlatformBase, naming_convention
from cloud_dog_db.models.mixins import AuditMixin, SoftDeleteMixin, TenantMixin, TimestampMixin


def test_naming_convention_keys():
    assert set(naming_convention) == {"ix", "uq", "ck", "fk", "pk"}


def test_platform_base_has_naming_convention():
    meta = PlatformBase.metadata
    assert meta.naming_convention is not None
    assert "ix" in meta.naming_convention
    assert "%(table_name)s" in meta.naming_convention["ix"]


class _SampleModel(PlatformBase):
    __tablename__ = "test_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)


def test_platform_base_creates_table():
    table = _SampleModel.__table__
    assert table.name == "test_samples"
    col_names = {c.name for c in table.columns}
    assert "id" in col_names
    assert "name" in col_names


class _TimestampedModel(PlatformBase, TimestampMixin):
    __tablename__ = "test_timestamped"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_timestamp_mixin_adds_columns():
    cols = {c.name for c in _TimestampedModel.__table__.columns}
    assert "created_at" in cols
    assert "updated_at" in cols
    created = _TimestampedModel.__table__.c.created_at
    assert isinstance(created.type, DateTime)
    assert created.type.timezone is True


class _SoftDeletable(PlatformBase, SoftDeleteMixin):
    __tablename__ = "test_soft_deletable"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_soft_delete_mixin_adds_columns():
    cols = {c.name for c in _SoftDeletable.__table__.columns}
    assert "deleted_at" in cols
    assert "is_deleted" in cols


class _Tenanted(PlatformBase, TenantMixin):
    __tablename__ = "test_tenanted"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_tenant_mixin_adds_column():
    cols = {c.name for c in _Tenanted.__table__.columns}
    assert "tenant_id" in cols
    tenant_col = _Tenanted.__table__.c.tenant_id
    assert isinstance(tenant_col.type, String)
    assert tenant_col.type.length == 64


class _Audited(PlatformBase, AuditMixin):
    __tablename__ = "test_audited"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_audit_mixin_adds_columns():
    cols = {c.name for c in _Audited.__table__.columns}
    assert "created_by" in cols
    assert "updated_by" in cols


class _FullModel(PlatformBase, TimestampMixin, SoftDeleteMixin, AuditMixin):
    __tablename__ = "test_full"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)


def test_combined_mixins():
    cols = {c.name for c in _FullModel.__table__.columns}
    expected = {"id", "title", "created_at", "updated_at", "deleted_at", "is_deleted", "created_by", "updated_by"}
    assert expected.issubset(cols)
