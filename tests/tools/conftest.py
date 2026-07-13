# ruff: noqa: E402
import sys
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).parents[2] / "tools" / "comprehension-uploader"
sys.path.insert(0, str(_TOOL_DIR))

from comprehension_uploader.db import Database


@pytest.fixture
def database() -> Generator[Callable[[str | Path], Database], None, None]:
    databases: list[Database] = []

    def create(path: str | Path) -> Database:
        db = Database(path)
        databases.append(db)
        return db

    yield create

    for db in databases:
        db.close()
