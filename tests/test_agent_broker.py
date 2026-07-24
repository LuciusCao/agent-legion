from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition, sync_agent_definitions
from server.app.agent_workers import AgentWorkerRegistry
from server.app.agents import AgentStatusManager
from tests.postgres_support import TEST_DATABASE_URL


def _seed_request(
    job_db,
    *,
    job_id: str,
    node_key: str = "generate",
    limit: int = 20,
    workspace_id: str = "test-workspace",
) -> None:
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    sync_agent_definitions(TEST_DATABASE_URL, {"generator-v1": definition})
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (?, 'Test') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (?, ?, 'questions', 'question', ?)",
            (job_id, workspace_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (?, ?)", (job_id, node_key))
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values (?, 'questions', ?, 'agent', 'generator-v1')"
            " on conflict(workspace_id, workflow_key, node_key) do nothing",
            (workspace_id, node_key),
        )
        # Capacity is workspace-level now: one row per workspace.
        conn.execute(
            "insert into workspace_agent_capacities(workspace_id, max_concurrency)"
            " values (?, ?)"
            " on conflict(workspace_id) do update"
            " set max_concurrency=excluded.max_concurrency",
            (workspace_id, limit),
        )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    assert broker.enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "pi": {"provider": "gateway", "model": "test-model"},
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
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

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
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

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
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

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
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

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

    assert AgentExecutionBroker(TEST_DATABASE_URL).claim("worker-1") is None
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
    broker = AgentExecutionBroker(TEST_DATABASE_URL, lease_ttl_seconds=1)
    first = broker.claim("worker-1")
    assert first is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=? where execution_id=?",
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
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

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
    broker = AgentExecutionBroker(TEST_DATABASE_URL, lease_ttl_seconds=1)
    claim = broker.claim("worker-1")
    assert claim is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=? where execution_id=?",
            (datetime.now(UTC) - timedelta(seconds=10), claim.execution_id),
        )
        # Simulate the result path having finished the lease already.
        conn.execute(
            "update executor_leases set status='released' where id=?",
            (claim.lease_id,),
        )

    assert broker.sweep_expired_claims() == []

    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=?",
            (claim.execution_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "done"


def test_reap_terminal_bundles_removes_done_bundles_and_stale_archives(job_db, tmp_path) -> None:
    _seed_request(job_db, job_id="job-1")
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    live_bundle = bundle_dir / "live.tar.gz"
    live_bundle.write_bytes(b"bundle")
    done_bundle = bundle_dir / "done.tar.gz"
    done_bundle.write_bytes(b"bundle")
    fresh_archive = bundle_dir / "fresh.result.tar.gz"
    fresh_archive.write_bytes(b"archive")
    stale_archive = bundle_dir / "stale.result.tar.gz"
    stale_archive.write_bytes(b"archive")
    old = datetime.now(UTC).timestamp() - 7200
    os.utime(stale_archive, (old, old))

    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir)
    with job_db.connect() as conn:
        # The seeded request stays queued (its bundle must survive); add a
        # terminal request pointing at done.tar.gz.
        conn.execute(
            "update agent_execution_requests set manifest_json=? where job_id='job-1'",
            (json.dumps({"bundle_name": "live.tar.gz"}),),
        )
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, workflow_key, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, manifest_json)"
            " values ('exec-done', 'test-workspace', 'job-1', 'questions', 'review',"
            " 'generator-v1', 'sha256:whatever', 1, 'done', current_timestamp, ?)",
            (json.dumps({"bundle_name": "done.tar.gz"}),),
        )

    reaped = broker.reap_terminal_bundles()

    assert reaped == 2
    assert live_bundle.is_file()
    assert not done_bundle.exists()
    assert fresh_archive.is_file()
    assert not stale_archive.exists()


def test_discard_result_archive_and_retire_bundle(tmp_path) -> None:
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    archive = bundle_dir / "exec-1.abc.result.tar.gz"
    archive.write_bytes(b"archive")
    bundle = bundle_dir / "bundle.tar.gz"
    bundle.write_bytes(b"bundle")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir)

    broker.discard_result_archive(archive.name)
    broker.retire_bundle(bundle.name)

    assert not archive.exists()
    assert not bundle.exists()

    # Missing files and unsafe bundle names are no-ops; a broker without
    # bundle storage tolerates both calls.
    broker.discard_result_archive("missing.result.tar.gz")
    broker.retire_bundle("../escape.tar.gz")
    storageless = AgentExecutionBroker(TEST_DATABASE_URL)
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
            "select token_hash from agent_register_tokens where id=?", (token_id,)
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
            "insert into workspaces(id, name) values ('other-workspace', 'Other')"
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
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

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
    broker = AgentExecutionBroker(
        TEST_DATABASE_URL, is_workspace_paused=lambda ws: paused.get(ws, False)
    )

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
    broker = AgentExecutionBroker(TEST_DATABASE_URL, agent_status=manager)

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
    broker = AgentExecutionBroker(TEST_DATABASE_URL, agent_status=manager)

    assert broker.claim("worker-1") is None

    # A global-scope Worker appears in every workspace's panel with 0/cap,
    # including ones where nothing is queued.
    rows = [a for a in manager.get_all() if a.id == "worker-1"]
    assert rows
    assert all(row.busy is False and row.task_count == 0 for row in rows)
    assert all(row.max_tasks == 10 for row in rows)


def test_swept_expired_claim_releases_worker_status_panel(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register_worker(registry)
    manager = AgentStatusManager()
    broker = AgentExecutionBroker(TEST_DATABASE_URL, lease_ttl_seconds=1, agent_status=manager)
    claimed = broker.claim("worker-1")
    assert claimed is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=? where execution_id=?",
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
    broker = AgentExecutionBroker(TEST_DATABASE_URL, job_db=job_db, job_event_buffer=buffer)

    assert broker.claim("worker-1") is not None

    assert buffer.records == [("test-workspace", "job-1")]
