from __future__ import annotations

import os
import re
from urllib.parse import quote


def _worker_schema() -> str:
    worker = re.sub(r"[^a-zA-Z0-9_]", "_", os.environ.get("PYTEST_XDIST_WORKER", "main"))
    return f"agent_legion_test_{worker}"


# Tests must never fall back to the ambient AGENT_LEGION_DATABASE_URL (or its
# deprecated VIDEO_HIVE_DATABASE_URL alias): those vars point at the dev/prod
# database in real shells, and agent loops exporting them have already wiped
# dev-schema state by running the suite against it. Only an explicit
# VIDEO_HIVE_TEST_DATABASE_URL may redirect the test database.
BASE_DATABASE_URL = os.environ.get(
    "VIDEO_HIVE_TEST_DATABASE_URL",
    "postgresql://127.0.0.1:5432/agent_legion_test",
)
TEST_SCHEMA = _worker_schema()
separator = "&" if "?" in BASE_DATABASE_URL else "?"
TEST_DATABASE_URL = (
    f"{BASE_DATABASE_URL}{separator}options={quote(f'-csearch_path={TEST_SCHEMA}', safe='')}"
)
