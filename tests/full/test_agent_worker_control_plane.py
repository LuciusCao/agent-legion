"""Full-gate checks for Agent Catalog, Worker security, and capacity domains."""

from __future__ import annotations

import hashlib

import pytest

from server.app.agent_workers import AgentWorkerRegistry
from tests.db.test_postgres_runtime import (
    test_schema_initialization_is_idempotent as _assert_schema_idempotent,
)
from tests.postgres_support import TEST_DATABASE_URL
from tests.test_agent_broker import (
    test_node_twenty_and_three_workers_ten_never_claim_more_than_twenty as _assert_capacity_matrix,
)
from tests.test_agent_catalog import (
    test_sync_agent_definitions_replaces_enabled_catalog as _assert_catalog_sync,
)


@pytest.mark.full_gate
def test_agent_capacity_matrix_across_workers(job_db) -> None:
    _assert_capacity_matrix(job_db)


@pytest.mark.full_gate
def test_agent_definition_catalog_snapshot_lifecycle(job_db) -> None:
    _assert_catalog_sync(job_db)


@pytest.mark.full_gate
def test_worker_token_is_hashed_and_revocable(job_db) -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    token = registry.issue_token(
        worker_id="secure-worker",
        name="Secure Worker",
        runtimes=["pi"],
        max_concurrency=2,
    )
    worker_id, secret = token.split(".", 1)
    with job_db.connect() as conn:
        row = conn.execute(
            "select token_hash from agent_workers where worker_id=?", (worker_id,)
        ).fetchone()

    assert row is not None
    assert row["token_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in row["token_hash"]
    assert registry.authenticate(token) is not None
    assert registry.revoke(worker_id)
    assert registry.authenticate(token) is None


@pytest.mark.full_gate
def test_postgres_agent_schema_initialization_is_idempotent() -> None:
    _assert_schema_idempotent()


@pytest.mark.full_gate
def test_scoped_register_token_lifecycle(job_db) -> None:
    """EXEC-WORKERACL-001 evidence: scoped tokens are hashed at rest, resolve
    to their workspace scope, stamp it onto registered Workers, and stop
    resolving after revoke."""
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('acl-workspace', 'ACL')"
            " on conflict(id) do nothing"
        )

    token_id, plaintext = registry.issue_register_token(
        workspace_id="acl-workspace", label="full-gate"
    )
    assert registry.resolve_register_scope(plaintext) == ["acl-workspace"]

    worker_token = registry.issue_token(
        worker_id="acl-worker",
        name="ACL Worker",
        runtimes=["pi"],
        max_concurrency=1,
        allowed_workspaces=registry.resolve_register_scope(plaintext),
    )
    worker = registry.authenticate(worker_token)
    assert worker is not None
    assert worker["allowed_workspaces"] == ["acl-workspace"]

    listed = registry.list_register_tokens()
    entry = next(item for item in listed if item["token_id"] == token_id)
    assert "token_hash" not in entry and "register_token" not in entry

    assert registry.revoke_register_token(token_id)
    assert registry.resolve_register_scope(plaintext) is None
