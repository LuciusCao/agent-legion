from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.dispatch import AgentDispatchService
from server.app.agent_catalog import AgentDefinition
from server.app.agent_workers import AgentWorkerRegistry
from server.app.events.agents import AgentStatusManager
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import Settings
from server.app.workflows.schema import WorkflowNode
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL


def _broker(data_dir, **kwargs) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir, **kwargs)


def _insert_job_rows(
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


def _seed_request(
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
    _insert_job_rows(
        job_db,
        job_id=job_id,
        node_key=node_key,
        limit=limit,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    assert _broker(job_db.jobs_dir.parent).enqueue(
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


def test_worker_registration_declares_runtime_and_machine_capacity(job_db) -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    token = registry.issue_token(
        worker_id="home-mini",
        name="Home Mac mini",
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )

    worker = registry.authenticate(token)

    assert worker is not None
    assert worker["runtimes"] == ["pi"]
    assert worker["max_concurrency"] == 10
    assert "capability" not in worker


def test_claim_starts_node_and_consumes_both_capacity_domains(job_db) -> None:
    _seed_request(job_db, job_id="job-1", limit=1)
    _seed_request(job_db, job_id="job-2", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )
    broker = _broker(job_db.jobs_dir.parent)

    first = broker.claim("worker-1")
    second = broker.claim("worker-1")

    assert first is not None
    assert second is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "running"
    assert job_db.get_job_node("job-2", "generate")["status"] == "pending"


def test_worker_machine_capacity_is_shared_across_nodes(job_db) -> None:
    _seed_request(job_db, job_id="job-1", node_key="generate-a", limit=20)
    _seed_request(job_db, job_id="job-2", node_key="generate-b", limit=20)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    broker = _broker(job_db.jobs_dir.parent)

    assert broker.claim("worker-1") is not None
    assert broker.claim("worker-1") is None


def test_claim_redeclares_live_worker_capacity(job_db) -> None:
    """A capacity re-declared on claim updates the Host-side worker record."""
    _seed_request(job_db, job_id="job-1", node_key="generate-a", limit=20)
    _seed_request(job_db, job_id="job-2", node_key="generate-b", limit=20)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    token = registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    broker = _broker(job_db.jobs_dir.parent)

    assert broker.claim("worker-1", 3) is not None
    # Registered capacity was 1; the live re-declaration raised it to 3.
    assert broker.claim("worker-1", 3) is not None
    worker = registry.authenticate(token)
    assert worker is not None
    assert worker["max_concurrency"] == 3


def test_workspace_capacity_is_shared_across_nodes(job_db) -> None:
    """One workspace-level cap governs all agent nodes of that workspace."""
    _seed_request(job_db, job_id="job-a1", node_key="generate-a", limit=1)
    _seed_request(job_db, job_id="job-b1", node_key="generate-b", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=2,
        labels={"arch": "arm64"},
    )
    broker = _broker(job_db.jobs_dir.parent)

    first = broker.claim("worker-1")
    second = broker.claim("worker-1")

    assert first is not None
    assert second is None
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select count(*) as cnt from agent_execution_requests where state='claimed'"
        ).fetchone()
    assert int(row["cnt"]) == 1


def test_incompatible_worker_does_not_claim_or_start_node(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["openclaw"],
        max_concurrency=10,
        labels={"arch": "amd64"},
    )

    assert _broker(job_db.jobs_dir.parent).claim("worker-1") is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "pending"


def test_expired_worker_claim_is_requeued_for_another_worker(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2"):
        registry.issue_token(
            worker_id=worker_id,
            name=worker_id,
            runtimes=["pi"],
            max_concurrency=1,
            labels={"arch": "arm64"},
        )
    broker = _broker(job_db.jobs_dir.parent, lease_ttl_seconds=1)
    first = broker.claim("worker-1")
    assert first is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), first.execution_id),
        )

    assert broker.sweep_expired_claims() == [first.execution_id]
    second = broker.claim("worker-2")

    assert second is not None
    assert second.execution_id == first.execution_id
    assert second.lease_id != first.lease_id


def test_node_twenty_and_three_workers_ten_never_claim_more_than_twenty(job_db) -> None:
    for index in range(30):
        _seed_request(job_db, job_id=f"job-{index}", limit=20)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2", "worker-3"):
        registry.issue_token(
            worker_id=worker_id,
            name=worker_id,
            runtimes=["pi"],
            max_concurrency=10,
            labels={"arch": "arm64"},
        )
    broker = _broker(job_db.jobs_dir.parent)

    claimed = [
        broker.claim(worker_id)
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


def test_sweep_closes_request_when_lease_already_finished(job_db) -> None:
    """A crash between finish() and mark_done() must not strand a claimed
    request or requeue a completed node: the sweep closes it instead."""
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    broker = _broker(job_db.jobs_dir.parent, lease_ttl_seconds=1)
    claim = broker.claim("worker-1")
    assert claim is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), claim.execution_id),
        )
        # Simulate the result path having finished the lease already.
        conn.execute(
            "update executor_leases set status='released' where id=%s",
            (claim.lease_id,),
        )

    assert broker.sweep_expired_claims() == []

    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claim.execution_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "done"


def test_discard_result_archive_and_retire_bundle(tmp_path) -> None:
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    archive = bundle_dir / "exec-1.abc.result.tar.gz"
    archive.write_bytes(b"archive")
    bundle = bundle_dir / "bundle.tar.gz"
    bundle.write_bytes(b"bundle")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)

    broker.discard_result_archive(archive.name)
    broker.retire_bundle(bundle.name)

    assert not archive.exists()
    assert not bundle.exists()

    # Missing files and unsafe bundle names are no-ops; a broker without
    # bundle storage tolerates both calls.
    broker.discard_result_archive("missing.result.tar.gz")
    broker.retire_bundle("../escape.tar.gz")
    storageless = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=tmp_path)
    storageless.discard_result_archive(archive.name)
    storageless.retire_bundle(bundle.name)


def test_scoped_register_token_lifecycle(job_db) -> None:
    """Issue stores only a sha256 hash and returns the plaintext once; list
    exposes no secret material; revoke makes the token unresolvable."""
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)

    token_id, plaintext = registry.issue_register_token(workspace_id="test-workspace", label="mini")
    assert plaintext.startswith(f"{token_id}.")
    secret = plaintext.split(".", 1)[1]

    with job_db._connect_read() as conn:
        row = conn.execute(
            "select token_hash from agent_register_tokens where id=%s", (token_id,)
        ).fetchone()
    assert row is not None
    assert row["token_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in row["token_hash"]

    listed = registry.list_register_tokens()
    entry = next(item for item in listed if item["token_id"] == token_id)
    assert entry["workspace_id"] == "test-workspace"
    assert entry["label"] == "mini"
    assert entry["revoked"] is False
    assert "token_hash" not in entry and "register_token" not in entry

    assert registry.resolve_register_scope(plaintext) == ["test-workspace"]
    assert registry.resolve_register_scope(f"{token_id}.wrong-secret") is None

    assert registry.revoke_register_token(token_id) is True
    assert registry.resolve_register_scope(plaintext) is None
    assert registry.revoke_register_token("no-such-token") is False


def test_register_token_for_missing_workspace_rejected(job_db) -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    with pytest.raises(ValueError, match="does not exist"):
        registry.issue_register_token(workspace_id="no-such-workspace")


def test_worker_registration_stores_and_refreshes_allowed_workspaces(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('other-workspace', 'Other', 'demo_workflow')"
            " on conflict(id) do nothing"
        )
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)

    token = registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=2,
        allowed_workspaces=["test-workspace"],
    )
    worker = registry.authenticate(token)
    assert worker is not None
    assert worker["allowed_workspaces"] == ["test-workspace"]

    # Re-registering with a different credential refreshes the stored scope.
    token = registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=2,
        allowed_workspaces=["other-workspace", "test-workspace"],
    )
    worker = registry.authenticate(token)
    assert worker is not None
    assert worker["allowed_workspaces"] == ["other-workspace", "test-workspace"]

    # Global credential (no scope) resets to all workspaces.
    token = registry.issue_token(
        worker_id="worker-1", name="worker", runtimes=["pi"], max_concurrency=2
    )
    worker = registry.authenticate(token)
    assert worker is not None
    assert worker["allowed_workspaces"] == []

    with pytest.raises(ValueError, match="does not exist"):
        registry.issue_token(
            worker_id="worker-2",
            name="worker",
            runtimes=["pi"],
            max_concurrency=1,
            allowed_workspaces=["no-such-workspace"],
        )


def test_scoped_worker_claims_only_its_workspace(job_db) -> None:
    """EXEC-WORKERACL-001: claim filters candidates by the server-stored
    registration scope; other workspaces' queued requests stay invisible."""
    _seed_request(job_db, job_id="job-a", workspace_id="test-workspace")
    _seed_request(job_db, job_id="job-b", workspace_id="other-workspace")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="scoped-worker",
        name="worker",
        runtimes=["pi"],
        max_concurrency=5,
        labels={"arch": "arm64"},
        allowed_workspaces=["test-workspace"],
    )
    broker = _broker(job_db.jobs_dir.parent)

    first = broker.claim("scoped-worker")
    assert first is not None
    assert first.workspace_id == "test-workspace"
    # The other workspace's request is not visible to this Worker.
    assert broker.claim("scoped-worker") is None
    assert job_db.get_job_node("job-b", "generate")["status"] == "pending"

    # A global-scope Worker ([]) still claims the other workspace's request.
    registry.issue_token(
        worker_id="global-worker",
        name="worker",
        runtimes=["pi"],
        max_concurrency=5,
        labels={"arch": "arm64"},
    )
    claimed = broker.claim("global-worker")
    assert claimed is not None
    assert claimed.workspace_id == "other-workspace"


def _register_worker(registry: AgentWorkerRegistry, worker_id: str = "worker-1") -> None:
    registry.issue_token(
        worker_id=worker_id,
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )


def test_paused_workspace_requests_are_not_claimed(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    paused = {"test-workspace": True}
    broker = _broker(job_db.jobs_dir.parent, is_workspace_paused=lambda ws: paused.get(ws, False))

    # Paused: the queued request stays queued and the node stays pending.
    assert broker.claim("worker-1") is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "pending"
    with job_db._connect_read() as conn:
        state = conn.execute(
            "select state from agent_execution_requests where job_id='job-1'"
        ).fetchone()
    assert state["state"] == "queued"

    # Resume: the same request becomes claimable.
    paused["test-workspace"] = False
    claimed = broker.claim("worker-1")
    assert claimed is not None
    assert claimed.workspace_id == "test-workspace"


def test_claim_and_done_mirror_worker_status_panel(job_db) -> None:
    _seed_request(job_db, job_id="job-1", limit=3)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    manager = AgentStatusManager()
    broker = _broker(job_db.jobs_dir.parent, agent_status=manager)

    claimed = broker.claim("worker-1")
    assert claimed is not None
    (row,) = [a for a in manager.get_all() if a.workspace_id == "test-workspace"]
    assert row.id == "worker-1"
    assert row.name == "worker"
    assert row.busy is True
    assert row.task_count == 1
    # max_tasks mirrors the Worker's machine capacity, not the workspace cap.
    assert row.max_tasks == 10

    broker.mark_done(claimed.execution_id, "worker-1", claimed.lease_id, {"status": "succeeded"})

    (row,) = [a for a in manager.get_all() if a.workspace_id == "test-workspace"]
    assert row.busy is False
    assert row.task_count == 0


def test_idle_claim_poll_registers_worker_panel_rows(job_db) -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    manager = AgentStatusManager()
    broker = _broker(job_db.jobs_dir.parent, agent_status=manager)
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('idle-workspace', 'Idle', 'demo_workflow')"
            " on conflict(id) do nothing"
        )

    assert broker.claim("worker-1") is None

    # A global-scope Worker appears in every workspace's panel with 0/cap,
    # including ones where nothing is queued.
    rows = [a for a in manager.get_all() if a.id == "worker-1"]
    assert [row.workspace_id for row in rows] == ["idle-workspace"]
    assert all(row.busy is False and row.task_count == 0 for row in rows)
    assert all(row.max_tasks == 10 for row in rows)


def test_swept_expired_claim_releases_worker_status_panel(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    manager = AgentStatusManager()
    broker = _broker(job_db.jobs_dir.parent, lease_ttl_seconds=1, agent_status=manager)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), claimed.execution_id),
        )

    assert broker.sweep_expired_claims() == [claimed.execution_id]

    (row,) = [
        a for a in manager.get_all() if a.id == "worker-1" and a.workspace_id == "test-workspace"
    ]
    assert row.busy is False
    assert row.task_count == 0


class _StubJobEventBuffer:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def record_job_updated(self, workspace_id: str, job_id: str) -> None:
        self.records.append((workspace_id, job_id))


def test_claim_records_job_update_for_live_list(job_db) -> None:
    """A Worker claim promotes the job queued -> running; the live job list
    only learns about it if the broker records a job event (finish already
    does). Without this the running filter only shrinks, never grows."""
    _seed_request(job_db, job_id="job-1", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    buffer = _StubJobEventBuffer()
    broker = _broker(job_db.jobs_dir.parent, job_db=job_db, job_event_buffer=buffer)

    assert broker.claim("worker-1") is not None

    assert buffer.records == [("test-workspace", "job-1")]


def test_release_slot_frees_worker_and_workspace_capacity(job_db) -> None:
    """reporting releases BOTH capacity domains: the worker slot and the
    workspace slot are counted from state='claimed' only."""
    _seed_request(job_db, job_id="job-1", limit=1)
    _seed_request(job_db, job_id="job-2", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    broker = _broker(job_db.jobs_dir.parent)

    first = broker.claim("worker-1")
    assert first is not None
    assert broker.claim("worker-1") is None  # worker capacity exhausted

    assert broker.release_slot(first.execution_id, "worker-1", first.lease_id) is True

    second = broker.claim("worker-1")
    assert second is not None
    assert second.execution_id != first.execution_id


def test_release_slot_requires_matching_lease_and_state(job_db) -> None:
    _seed_request(job_db, job_id="job-1", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    broker = _broker(job_db.jobs_dir.parent)
    claimed = broker.claim("worker-1")
    assert claimed is not None

    assert broker.release_slot(claimed.execution_id, "worker-1", "wrong-lease") is False
    assert broker.release_slot(claimed.execution_id, "worker-2", claimed.lease_id) is False
    # Still claimed afterwards: capacity is NOT released by failed attempts.
    assert broker.claim("worker-1") is None

    assert broker.release_slot(claimed.execution_id, "worker-1", claimed.lease_id) is True
    # A second release is a no-op (already reporting, not claimed).
    assert broker.release_slot(claimed.execution_id, "worker-1", claimed.lease_id) is False


def test_heartbeat_and_mark_done_accept_reporting_state(job_db) -> None:
    _seed_request(job_db, job_id="job-1", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    broker = _broker(job_db.jobs_dir.parent)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    assert broker.release_slot(claimed.execution_id, "worker-1", claimed.lease_id) is True

    assert broker.heartbeat(claimed.execution_id, "worker-1", claimed.lease_id) is True
    assert broker.claimed_payload(claimed.execution_id, "worker-1") is not None
    assert (
        broker.mark_done(
            claimed.execution_id, "worker-1", claimed.lease_id, {"status": "completed"}
        )
        == claimed.lease_id
    )
    with job_db.connect() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claimed.execution_id,),
        ).fetchone()
    assert row["state"] == "done"


def test_heartbeat_returns_false_when_lease_no_longer_active(job_db) -> None:
    """A finish-path release racing the heartbeat must not be reported as
    success: the lease row is not active, so the Worker is told to stop
    instead of keeping a zombie attempt alive."""
    _seed_request(job_db, job_id="job-1", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    broker = _broker(job_db.jobs_dir.parent)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    with job_db.connect() as conn:
        conn.execute(
            "update executor_leases set status='released' where id=%s",
            (claimed.lease_id,),
        )

    assert broker.heartbeat(claimed.execution_id, "worker-1", claimed.lease_id) is False


def test_sweep_requeue_limit_failure_message_carries_context(job_db, caplog) -> None:
    """The terminal requeue-limit failure must name the execution, worker and
    attempt numbers; the lease deletion (the repo's only DELETE path) must
    leave an audit log record."""
    _seed_request(job_db, job_id="job-1", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    broker = _broker(job_db.jobs_dir.parent, lease_ttl_seconds=1, requeue_limit=0)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=10), claimed.execution_id),
        )

    with caplog.at_level(logging.WARNING, logger="server.app.agent_broker.sweepers"):
        assert broker.sweep_expired_claims() == []

    node = job_db.get_job_node("job-1", "generate")
    assert node["status"] == "failed"
    message = node["error_message"]
    assert "requeue limit exceeded" in message
    assert claimed.execution_id in message
    assert "worker-1" in message
    assert "attempt=1" in message
    assert "limit=0" in message
    assert any(
        "deleting expired agent lease" in record.message
        and claimed.lease_id in record.message
        and claimed.execution_id in record.message
        and "job-1" in record.message
        and "worker-1" in record.message
        for record in caplog.records
    )


def test_reporting_blocks_duplicate_enqueue(job_db) -> None:
    _seed_request(job_db, job_id="job-1", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    broker = _broker(job_db.jobs_dir.parent)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    assert broker.release_slot(claimed.execution_id, "worker-1", claimed.lease_id) is True

    assert broker.has_active_request("job-1", "generate") is True
    # The partial unique index covers reporting: a second enqueue for the
    # same node is rejected while the first result is still uploading.
    assert (
        broker.enqueue(
            AgentExecutionRequest(
                workspace_id="test-workspace",
                job_id="job-1",
                workflow_key="questions",
                node_key="generate",
                agent_id="generator-v1",
                agent_definition_hash=_seeded_definition_hash(),
                manifest={
                    "job_id": "job-1",
                    "execution": {"provider": "gateway", "model": "test-model"},
                },
            )
        )
        is None
    )


def _seeded_definition_hash() -> str:
    """Match the definition registered by _seed_request (labels included)."""
    return AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    ).definition_hash()


def test_sweep_requeues_reporting_with_expired_heartbeat(job_db) -> None:
    """A Worker that dies mid-upload is the same as dying mid-run: the claim
    is requeued and the half-finished node run is failed."""
    _seed_request(job_db, job_id="job-1", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    broker = _broker(job_db.jobs_dir.parent, lease_ttl_seconds=90)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    assert broker.release_slot(claimed.execution_id, "worker-1", claimed.lease_id) is True
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=%s where execution_id=%s",
            (datetime.now(UTC) - timedelta(seconds=200), claimed.execution_id),
        )

    assert broker.sweep_expired_claims() == [claimed.execution_id]

    with job_db.connect() as conn:
        row = conn.execute(
            "select state, worker_id, lease_id from agent_execution_requests where execution_id=%s",
            (claimed.execution_id,),
        ).fetchone()
    assert row["state"] == "queued"
    assert row["worker_id"] is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "pending"


def _register_runtime_worker(
    registry: AgentWorkerRegistry, worker_id: str, runtimes: list[str]
) -> None:
    registry.issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=runtimes,
        max_concurrency=10,
        labels={"arch": "arm64"},
    )


def test_pi_only_worker_cannot_claim_velites_request(job_db) -> None:
    """Runtime matching lives in claim.py: a Worker that never declared
    velites skips velites requests; a velites Worker picks them up."""
    _seed_request(job_db, job_id="job-1", runtime="velites")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_runtime_worker(registry, "worker-pi", ["pi"])
    _register_runtime_worker(registry, "worker-velites", ["velites"])
    broker = _broker(job_db.jobs_dir.parent)

    assert broker.claim("worker-pi") is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "pending"

    claimed = broker.claim("worker-velites")
    assert claimed is not None
    assert claimed.job_id == "job-1"
    assert job_db.get_job_node("job-1", "generate")["status"] == "running"


def test_mixed_runtime_fleet_claims_matching_requests(job_db) -> None:
    """Dual-runtime coexistence: pi and velites definitions side by side, each
    Worker claims exactly the requests of its declared runtime.

    Capabilities differ per definition: one published Agent per capability is
    enforced by the DB partial unique index, so a same-capability dual-runtime
    catalog is no longer representable.
    """
    pi_definition = AgentDefinition(
        capability="generate_pi",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    velites_definition = AgentDefinition(
        capability="generate_velites",
        runtime="velites",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    catalog = {"gen-pi": pi_definition, "gen-velites": velites_definition}
    _seed_request(
        job_db,
        job_id="job-pi",
        node_key="generate-a",
        runtime="pi",
        agent_id="gen-pi",
        definitions=catalog,
    )
    _seed_request(
        job_db,
        job_id="job-velites",
        node_key="generate-b",
        runtime="velites",
        agent_id="gen-velites",
        definitions=catalog,
    )
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_runtime_worker(registry, "worker-pi", ["pi"])
    _register_runtime_worker(registry, "worker-velites", ["pi", "velites"])
    broker = _broker(job_db.jobs_dir.parent)

    claimed_pi = broker.claim("worker-pi")
    assert claimed_pi is not None
    assert claimed_pi.job_id == "job-pi"
    # The pi-only Worker never touches the velites request.
    assert broker.claim("worker-pi") is None

    claimed_velites = broker.claim("worker-velites")
    assert claimed_velites is not None
    assert claimed_velites.job_id == "job-velites"
    assert job_db.get_job_node("job-velites", "generate-b")["status"] == "running"


def test_stale_pi_and_fresh_velites_requests_coexist_during_migration(job_db) -> None:
    """Definition migration (runtime pi -> velites) changes definition_hash:
    the queued request pinned to the old hash is failed by the stale sweeper
    while the freshly pinned velites request claims normally."""
    _seed_request(job_db, job_id="job-old", runtime="pi")
    # Migrate the definition in place: same agent_id, new runtime => new hash.
    velites_definition = AgentDefinition(
        capability="generate",
        runtime="velites",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    replace_agent_catalog("test-workspace", {"generator-v1": velites_definition})
    _insert_job_rows(
        job_db,
        job_id="job-new",
        node_key="generate",
        limit=20,
        workspace_id="test-workspace",
        agent_id="generator-v1",
    )
    broker = _broker(job_db.jobs_dir.parent)
    fresh_execution_id = broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id="job-new",
            workflow_key="questions",
            node_key="generate",
            agent_id="generator-v1",
            agent_definition_hash=velites_definition.definition_hash(),
            manifest={
                "job_id": "job-new",
                "log_path": "logs/job-new.log",
                "execution": {"provider": "gateway", "model": "test-model"},
            },
        )
    )
    assert fresh_execution_id is not None
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_runtime_worker(registry, "worker-velites", ["velites"])

    stale = broker.fail_stale_definition_requests()
    assert len(stale) == 1
    assert stale != [fresh_execution_id]
    assert job_db.get_job_node("job-old", "generate")["status"] == "failed"

    claimed = broker.claim("worker-velites")
    assert claimed is not None
    assert claimed.execution_id == fresh_execution_id
    assert claimed.job_id == "job-new"


def test_dispatch_fails_fast_on_unsupported_runtime(job_db, tmp_path) -> None:
    """EXEC-RUNTIME-DISPATCH-001: openclaw stays fail-fast at dispatch; the
    error names the supported runtime set."""
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )
    broker = _broker(tmp_path, bundle_dir=tmp_path / "bundles")
    store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
    service = AgentDispatchService(settings, broker, store)
    definition = AgentDefinition(
        capability="generate",
        runtime="openclaw",
        skill="question/generate",
    )
    node = WorkflowNode(key="generate", label="generate", capability="generate", outputs=["o.json"])

    with pytest.raises(ValueError, match=r"supported runtimes: pi, velites"):
        service.enqueue(
            agent_id="generator-v1",
            definition=definition,
            workspace={"id": "test-workspace"},
            job={"id": "job-openclaw"},
            workflow_key="questions",
            node=node,
            job_dir=tmp_path / "job",
            log_path=tmp_path / "job.log",
            inputs=(),
        )
