"""Shared helpers for the agent worker / broker API test surface.

These grew inside test files (``tests/test_agent_broker.py``,
``tests/routes/test_agent_workers.py``) and were then imported cross-file by
other tests, turning test modules into each other's dependencies. They live
here so a test file can be renamed or split without a cascade of import
errors.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.main import create_app
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL


def broker(data_dir, **kwargs) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir, **kwargs)


def assert_capacity_matrix(job_db) -> None:
    """30 queued nodes, 3 workers capped at 10: exactly 20 claims get through.

    Shared by the unit tier (tests/test_agent_broker.py) and the full-gate
    evidence (tests/full/test_agent_worker_control_plane.py).
    """
    for index in range(30):
        seed_request(job_db, job_id=f"job-{index}", limit=20)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2", "worker-3"):
        registry.issue_token(
            worker_id=worker_id,
            name=worker_id,
            runtimes=["pi"],
            max_concurrency=10,
            labels={"arch": "arm64"},
        )
    executor = broker(job_db.jobs_dir.parent)

    claimed = [
        executor.claim(worker_id)
        for worker_id in ("worker-1", "worker-2", "worker-3")
        for _ in range(10)
    ]

    assert sum(claim is not None for claim in claimed) == 20
    with job_db._connect_read() as conn:
        rows = conn.execute(
            "select worker_id, count(*) as cnt from agent_execution_requests"
            " where state='claimed' group by worker_id order by worker_id"
        ).fetchall()
    assert sum(int(row["cnt"]) for row in rows) == 20
    assert all(int(row["cnt"]) <= 10 for row in rows)


def insert_job_rows(
    job_db,
    *,
    job_id: str,
    node_key: str,
    limit: int,
    workspace_id: str,
    agent_id: str,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, 'Test', 'demo_workflow') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, %s, 'questions', 'question', %s)",
            (job_id, workspace_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values (%s, 'questions', %s, 'agent', %s)"
            " on conflict(workspace_id, workflow_key, node_key) do nothing",
            (workspace_id, node_key, agent_id),
        )
        # Capacity is workspace-level now: one row per workspace.
        conn.execute(
            "insert into workspace_agent_capacities(workspace_id, max_concurrency)"
            " values (%s, %s)"
            " on conflict(workspace_id) do update"
            " set max_concurrency=excluded.max_concurrency",
            (workspace_id, limit),
        )


def seed_request(
    job_db,
    *,
    job_id: str,
    node_key: str = "generate",
    limit: int = 20,
    workspace_id: str = "test-workspace",
    runtime: str = "pi",
    agent_id: str = "generator-v1",
    definitions: dict[str, AgentDefinition] | None = None,
) -> None:
    definition = AgentDefinition(
        capability="generate",
        runtime=runtime,
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    catalog = definitions or {agent_id: definition}
    replace_agent_catalog(workspace_id, catalog)
    insert_job_rows(
        job_db,
        job_id=job_id,
        node_key=node_key,
        limit=limit,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    assert broker(job_db.jobs_dir.parent).enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            agent_id=agent_id,
            agent_definition_hash=catalog[agent_id].definition_hash(),
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "execution": {"provider": "gateway", "model": "test-model"},
            },
        )
    )


def authenticate_admin(client: TestClient) -> None:
    """Bootstrap the first admin and keep its session cookie on the client.

    409 = first user already bootstrapped on this app (a second TestClient
    block within one test); the cookie is per-client so re-login instead."""
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "admin", "password": "admin-pw"},
    )
    if response.status_code == 409:
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin-pw"},
        )
    assert response.status_code == 200, response.text
    client.headers["x-agent-legion-request"] = "1"


def make_app(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    # Workspace dispatch defaults to paused (reset at every startup); the
    # operator resume is part of the environment these tests exercise.
    app.state.workspace_worker_control.resume("test-workspace")
    return app


def issue_scoped_token(client: TestClient, workspace_id: str = "test-workspace") -> str:
    """Issue a real workspace-scoped register token via the admin API.

    Creates the workspace first when it does not exist yet (some tests never
    seed a job and only exercise the registration contract)."""
    # 签发是 admin-only API；幂等（409 → login 回落），重复调用无副作用。
    authenticate_admin(client)
    created = client.post(
        "/api/agent-register-tokens",
        json={"workspace_id": workspace_id, "label": "test"},
    )
    if created.status_code == 400:
        ensured = client.post(
            "/api/workspaces",
            json={"id": workspace_id, "name": workspace_id},
        )
        assert ensured.status_code in (200, 201), ensured.text
        created = client.post(
            "/api/agent-register-tokens",
            json={"workspace_id": workspace_id, "label": "test"},
        )
    assert created.status_code == 201, created.text
    return created.json()["register_token"]


def register(client: TestClient, credential: str | None = None, **overrides) -> dict:
    """Register a worker with a scoped token (auto-issued for test-workspace)."""
    if credential is None:
        credential = issue_scoped_token(client)
    payload = {
        "worker_id": "home-mini",
        "name": "Home Mac mini",
        "runtimes": ["pi"],
        "capabilities": ["generate"],
        "models": [{"provider": "gateway", "model": "test-model"}],
        "max_concurrency": 10,
        "labels": {"arch": "arm64"},
        "protocol_version": 1,
        "image_version": "agent-legion-worker:test",
    }
    # tokens is a header-level option, not a payload field: pop it before the
    # update so it never leaks into the JSON body.
    tokens = overrides.pop("tokens", None)
    payload.update(overrides)
    headers = {"X-Agent-Worker-Register-Token": credential}
    if tokens:
        headers = {"X-Agent-Worker-Register-Tokens": ",".join(tokens)}
    response = client.post(
        "/api/agent-workers/register",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    assert response.json()["host_protocol_version"] == 3
    return dict(response.json())


def claim(client: TestClient, token: str) -> dict:
    response = client.post(
        "/api/agent-executions/claim",
        headers={"X-Agent-Worker-Token": token},
        json={"worker_id": "home-mini"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def empty_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz"):
        pass
    return buffer.getvalue()


def insert_code_job_rows(job_db, *, job_id: str, node_key: str = "package") -> None:
    """Minimal job rows for the kind='code' claim path (no agent routing)."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'demo_workflow')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'test-workspace', 'questions', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


def enqueue_code(broker: AgentExecutionBroker, *, job_id: str, node_key: str = "package") -> str:
    """Enqueue one kind='code' request: no Agent definition exists for this
    pair, so the claim must not consult versioned_entities for code rows."""
    execution_id = broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            agent_id="package",
            agent_definition_hash="codehash",
            manifest={
                "kind": "code",
                "capability": "package",
                "code_hash": "abc123",
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "config": {"mode": "fast"},
            },
            kind="code",
        )
    )
    assert execution_id is not None
    return execution_id
