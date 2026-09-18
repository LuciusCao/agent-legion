"""#626 boundary pins: the workspace API token store stays facade-only.

P1 fix guard (PR #704 review): ``WorkspaceApiTokenStore`` is the auth
semantics layer — hashing, hmac comparison, TTL interpretation and the
last_used_at throttle. Its persistence must go through the JobQueries
facade (BOUNDARY-DATA-001, AGENTS.md data-access boundary), never through
direct transaction-layer imports or hand-written SQL in the auth component.
The service_data_boundary ratchet does not scan ``server/app/auth`` yet,
so this test pins the same rule for this file until the ratchet's coverage
catches up.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / "server/app/auth/workspace_api_tokens.py"

# Transaction-layer escapes the store must not reintroduce (the same shape
# scripts/architecture/service_data_boundary.py counts under services).
_FORBIDDEN_FRAGMENTS = (
    "from server.app.db.transaction import",
    "from server.app.db import transaction",
    "from server.app.db.connection import",
    "from server.app.db import connection",
    "read_connection",
    "write_transaction",
    "connect_database",
    # The pre-fix constructor held a bare DSN / ConnectSource — the facade
    # replaced it, and a string DSN here would reopen the bypass.
    "ConnectSource",
    "database_dsn",
)

_FACADE_METHODS = (
    "create_workspace_api_token",
    "get_workspace_api_token_row",
    "update_workspace_api_token_last_used",
    "list_workspace_api_tokens",
    "revoke_workspace_api_token",
)


def test_store_module_has_no_transaction_layer_escape() -> None:
    source = STORE_PATH.read_text(encoding="utf-8")
    offenders = [f for f in _FORBIDDEN_FRAGMENTS if f in source]
    assert not offenders, (
        "WorkspaceApiTokenStore must reach the database only through the "
        "JobQueries facade (BOUNDARY-DATA-001); forbidden fragments found: " + ", ".join(offenders)
    )


def test_facade_owns_the_workspace_api_token_persistence() -> None:
    """The data-access methods resolve on JobQueries itself (not just on
    the store) — a mixin dropped from queries/groups.py would silently
    strand the store's calls."""
    queries_cls = importlib.import_module("server.app.jobs.queries").JobQueries
    missing = [m for m in _FACADE_METHODS if not hasattr(queries_cls, m)]
    assert not missing, f"JobQueries lost workspace API token methods: {missing}"


def test_facade_module_is_the_sql_home() -> None:
    """The SQL lives in the queries mixin, and the store calls the facade
    method for every persistence shape (no leftover SQL keywords)."""
    queries_path = ROOT / "server/app/jobs/queries/workspace_api_tokens.py"
    assert queries_path.exists(), "queries/workspace_api_tokens.py went missing"
    store_source = STORE_PATH.read_text(encoding="utf-8")
    for keyword in ("select ", "insert into", "update workspace_api_tokens"):
        assert keyword not in store_source, f"SQL leaked back into the store: {keyword!r}"
