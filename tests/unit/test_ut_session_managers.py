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
from sqlalchemy import text

from cloud_dog_db.config.models import DatabaseSettings
from cloud_dog_db.engine.factory import build_async_engine, build_sync_engine
from cloud_dog_db.session.session_manager import AsyncSessionManager, SyncSessionManager


def test_sync_session_manager_round_trip() -> None:
    engine = build_sync_engine(DatabaseSettings(dialect="sqlite", database=":memory:"))
    mgr = SyncSessionManager(engine)
    with mgr.session() as session:
        value = session.execute(text("SELECT 1")).scalar_one()
    assert value == 1


@pytest.mark.asyncio
async def test_async_session_manager_round_trip() -> None:
    engine = build_async_engine(DatabaseSettings(dialect="sqlite", database=":memory:"))
    mgr = AsyncSessionManager(engine)
    async with mgr.session() as session:
        result = await session.execute(text("SELECT 1"))
        value = result.scalar_one()
    assert value == 1
