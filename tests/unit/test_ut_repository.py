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
from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from cloud_dog_db.crud.repository import ConflictError, Repository
from cloud_dog_db.crud.specs import FilterOperator, FilterSpec, PageSpec, QuerySpec, SortSpec


class Base(DeclarativeBase):
    pass


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="new")


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_repository_crud_and_listing(session: Session) -> None:
    repo = Repository(Widget, session)
    repo.create({"name": "alpha", "status": "new"})
    repo.create({"name": "beta", "status": "done"})

    page = repo.list(
        QuerySpec(
            filters=[FilterSpec(field="status", operator=FilterOperator.EQ, value="new")],
            sorts=[SortSpec(field="name", descending=False)],
            page=PageSpec(limit=10, offset=0),
        )
    )
    assert page.total == 1
    assert page.items[0].name == "alpha"

    first = repo.get(1)
    assert first.name == "alpha"
    updated = repo.update(1, {"status": "done"})
    assert updated.status == "done"


def test_repository_conflict_error(session: Session) -> None:
    repo = Repository(Widget, session)
    repo.create({"name": "dup", "status": "new"})
    with pytest.raises(ConflictError):
        repo.create({"name": "dup", "status": "new"})
