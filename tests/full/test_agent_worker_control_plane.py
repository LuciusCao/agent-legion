"""Full-gate checks for Agent Catalog, Worker security, and capacity domains."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.agent_catalog import AgentDefinition
from server.app.agent_workers import AgentWorkerRegistry
from server.app.main import create_app
from server.app.services.agent_service import published_agent_definitions
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.db.test_postgres_runtime import (
    test_schema_initialization_is_idempotent as _assert_schema_idempotent,
)
from tests.helpers import load_builtin_definition, replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL
from tests.test_agent_broker import _seed_request
from tests.test_agent_broker import (
    test_node_twenty_and_three_workers_ten_never_claim_more_than_twenty as _assert_capacity_matrix,
)


@pytest.mark.full_gate
def test_agent_capacity_matrix_across_workers(job_db) -> None:
    _assert_capacity_matrix(job_db)


@pytest.mark.full_gate
def test_agent_definition_catalog_snapshot_lifecycle(job_db) -> None:
    """Publish flow replaces the published catalog; reads enforce exact hashes."""
    first = AgentDefinition(capability="generate", runtime="pi", skill="question/generate")
    second = AgentDefinition(capability="review", runtime="openclaw", skill="question/review")
    replace_agent_catalog({"generator-v1": first, "reviewer-v1": second})

    catalog = published_agent_definitions(TEST_DATABASE_URL)
    assert catalog == {"generator-v1": first, "reviewer-v1": second}

    replace_agent_catalog({"reviewer-v1": second})

    catalog = published_agent_definitions(TEST_DATABASE_URL)
    assert catalog == {"reviewer-v1": second}


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
            "select token_hash from agent_workers where worker_id=%s", (worker_id,)
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


@pytest.mark.full_gate
def test_startup_materializes_agent_routes(client, job_db) -> None:
    """Startup wiring: the published catalog is visible, and an explicitly
    created workspace's active revision gets its Agent routes materialized —
    the two states whose loss caused the 'Executor pi is not registered'
    incident. No workspace is seeded at startup; the fixture workspace is
    created and published here, then the startup reconcile is replayed."""
    expected_agents = set(published_agent_definitions(TEST_DATABASE_URL))
    assert expected_agents, "test requires a non-empty published Agent catalog"

    workspace = job_db.create_workspace(
        "Route Check", default_workflow_key="question_comprehension_info"
    )
    workspace_id = workspace["id"]
    definition = load_builtin_definition("question_comprehension_info")
    revision_service = WorkflowRevisionService(job_db)
    revision_service.publish_workspace_revision(workspace_id, definition)
    revision_service.reconcile_active_agent_routes()

    with job_db._connect_read() as conn:
        published = {
            row["entity_key"]
            for row in conn.execute(
                "select entity_key from versioned_entities"
                " where entity_type='agent' and workspace_id is null and status='published'"
            ).fetchall()
        }
        routes = conn.execute(
            "select node_key, target_id from workspace_node_routes"
            " where workspace_id=%s and target_kind='agent'",
            (workspace_id,),
        ).fetchall()

    assert published == expected_agents
    assert routes, "startup reconcile must materialize routes for the active revision"
    assert {row["target_id"] for row in routes} <= expected_agents


@pytest.mark.full_gate
def test_http_claim_cycle_releases_capacity_and_updates_panel(tmp_path: Path) -> None:
    """Control-plane ↔ Worker seam: register → claim → heartbeat → result over
    HTTP must close the request, release both capacity domains, complete the
    node, and mirror busy/idle into the workspace Agent panel."""
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.agent_workers.register_token = "management-secret"
    # Dispatch defaults to paused after every startup; resume the seeded
    # workspace the way an operator would.
    app.state.workspace_worker_control.resume("test-workspace")
    _seed_request(app.state.job_db, job_id="job-e2e", limit=2)

    with TestClient(app) as client:
        token = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": "management-secret"},
            json={
                "worker_id": "e2e-worker",
                "name": "E2E Worker",
                "runtimes": ["pi"],
                "capabilities": ["generate"],
                "models": [{"provider": "gateway", "model": "test-model"}],
                "max_concurrency": 4,
                "labels": {"arch": "arm64"},
                "protocol_version": 1,
            },
        ).json()["worker_token"]
        auth = {"X-Agent-Worker-Token": token}

        claimed_response = client.post(
            "/api/agent-executions/claim", headers=auth, json={"worker_id": "e2e-worker"}
        )
        assert claimed_response.status_code == 200, claimed_response.text
        claimed = claimed_response.json()
        lease_auth = {**auth, "X-Agent-Lease-Id": claimed["lease_id"]}

        heartbeat = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/heartbeat",
            headers=lease_auth,
        )
        assert heartbeat.status_code == 204

        manager = app.state.agent_manager
        (busy_row,) = [a for a in manager.get_all() if a.workspace_id == "test-workspace"]
        assert busy_row.id == "e2e-worker"
        assert busy_row.busy is True
        assert busy_row.task_count == 1
        assert busy_row.max_tasks == 4

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz"):
            pass
        # The scheduler normally creates the job dir at dispatch time; the
        # seeded job went straight to the broker, so create it here.
        (app.state.settings.jobs_dir / "job-e2e").mkdir(parents=True, exist_ok=True)
        result = client.post(
            f"/api/agent-executions/{claimed['execution_id']}/result",
            headers={**lease_auth, "X-Agent-Result": json.dumps({"status": "completed"})},
            content=buffer.getvalue(),
        )
        assert result.status_code == 204, result.text

    with app.state.job_db._connect_read() as conn:
        request = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claimed["execution_id"],),
        ).fetchone()
        claimed_count = conn.execute(
            "select count(*) as c from agent_execution_requests where state='claimed'"
        ).fetchone()
        active_leases = conn.execute(
            "select count(*) as c from executor_leases where status='active'"
        ).fetchone()
    assert request["state"] == "done"
    assert claimed_count["c"] == 0
    assert active_leases["c"] == 0
    assert app.state.job_db.get_job_node("job-e2e", "generate")["status"] == "completed"

    (row,) = [a for a in manager.get_all() if a.workspace_id == "test-workspace"]
    assert row.busy is False
    assert row.task_count == 0
