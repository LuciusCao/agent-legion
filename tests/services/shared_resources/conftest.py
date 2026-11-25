from __future__ import annotations

import pytest

from server.app.services.materials import MaterialsService
from server.app.services.shared_resources.service import SharedResourcesService
from tests.fakes.storage import FakeObjectStorage

SOURCE_WS = "shared-source"
TARGET_WS = "shared-target"
THIRD_WS = "shared-third"


@pytest.fixture
def shared_workspaces(job_db):
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Source'), (%s, 'Target'), (%s, 'Third')",
            (SOURCE_WS, TARGET_WS, THIRD_WS),
        )


@pytest.fixture
def shared_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def shared_materials(job_db, shared_storage, shared_workspaces) -> MaterialsService:
    return MaterialsService(job_db, shared_storage)


@pytest.fixture
def shared_service(job_db, shared_storage, shared_workspaces) -> SharedResourcesService:
    return SharedResourcesService(job_db, shared_storage)
