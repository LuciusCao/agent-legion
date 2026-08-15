from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from server.app.jobs import JobQueries
from server.app.services.workflow_revision_format import serialize_definition
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from server.app.workflows.execution_control import allowed_nodes
from tests.helpers import make_workflow_worker
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import (
    RecordingExecutor,
    _local_node,
    _make_definition,
    _make_worker,
)


def test_worker_creates_shared_pool_per_executor_id(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()

    assert "code-default" in worker._pools
    assert worker._pools["code-default"]._max_workers == 2
    # No per-workspace pools should exist
    assert not hasattr(worker, "_ws_local_executors") or not worker._ws_local_executors
    assert not hasattr(worker, "_ws_agent_executors") or not worker._ws_agent_executors

    worker.stop()


def test_poll_submits_ready_local_node(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code-default")
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (ws["id"], "test", "fetch", "code-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is True
    assert worker.leases.active_counts("code-default").get("global", 0) == 1
    assert len(worker._futures) == 1

    executor.block_event.set()
    worker.stop()


def test_poll_skips_duplicate_submissions(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    block_event = threading.Event()
    executor = RecordingExecutor("code-default", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (ws["id"], "test", "fetch", "code-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()
    assert worker.leases.active_counts("code-default").get("global", 0) == 1

    worker._poll()
    assert worker.leases.active_counts("code-default").get("global", 0) == 1

    block_event.set()
    worker.stop()


def test_poll_skips_paused_workspace(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code-default")
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (ws["id"], "test", "fetch", "code-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    control = MagicMock()
    control.is_paused = lambda ws_id: ws_id == ws["id"]
    worker.workspace_worker_control = control

    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("code-default").get("global", 0) == 0

    worker.stop()


def test_poll_fails_node_without_binding(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code-default")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node is not None
    assert node["status"] == "failed"
    assert "No Executor binding" in node["error_message"]

    worker.stop()


def test_poll_fails_node_with_unsupported_capability(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code-default")
    executor.supports = lambda capability: capability == "other"  # type: ignore[method-assign]
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (ws["id"], "test", "fetch", "code-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node is not None
    assert node["status"] == "failed"
    assert "does not support capability" in node["error_message"]

    worker.stop()


def test_stop_shuts_down_shared_pools(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()
    pool = worker._pools["code-default"]
    worker.stop()

    assert pool._shutdown is True


def test_poll_skips_paused_job(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code-default")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    job_db.pause_job(job["id"], "awaiting_resources")
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (ws["id"], "test", "fetch", "code-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("code-default").get("global", 0) == 0
    assert len(worker._futures) == 0

    worker.stop()


def test_poll_runs_only_target_closure_in_until_node_mode(tmp_path: Path) -> None:

    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code-default")
    definition = _make_definition(
        [
            _local_node("root"),
            WorkflowNode(key="left", label="left", capability="left", after=["root"]),
            WorkflowNode(key="target", label="target", capability="target", after=["left"]),
            WorkflowNode(key="right", label="right", capability="right", after=["root"]),
        ]
    )

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["root", "left", "target", "right"],
        workspace_id=ws["id"],
    )
    job_db.set_job_execution_target(job["id"], "target")
    with job_db.connect() as conn:
        for node in definition.nodes.values():
            conn.execute(
                "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
                (ws["id"], "test", node.key, "code-default"),
            )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (%s, %s, %s, %s)",
            (ws["id"], "test", "root", 1),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (%s, %s, %s, %s)",
            (ws["id"], "test", "left", 1),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (%s, %s, %s, %s)",
            (ws["id"], "test", "target", 1),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    executor.block_event.set()

    for _ in range(20):
        worker._poll()
        job_after = job_db.get_job(job["id"])
        if job_after and job_after["status"] in ("paused", "completed"):
            break

    worker.stop()

    statuses = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    allowed = allowed_nodes(
        definition,
        {"execution_mode": "until_node", "target_node_key": "target"},
    )
    for key in allowed:
        assert statuses[key] == "completed"
    assert statuses["right"] == "pending"

    job_after = job_db.get_job(job["id"])
    assert job_after is not None
    assert job_after["status"] == "paused"
    assert job_after["execution_paused"] == 1
    assert job_after["pause_reason"] == "target_reached"


def test_make_workflow_worker_runs_demo_intake_local_node(tmp_path: Path, monkeypatch) -> None:
    # intake_knowledge_points runs on the code-default executor in an isolated
    # child process: it maps the job's source_id to a knowledge-point markdown
    # under examples/education-video-problems-generation/ (pure stdlib, no
    # network) and writes knowledge_point.json for the downstream agent nodes.
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    worker, definition = make_workflow_worker(tmp_path, queries)
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="education_video_problems_generation"
    )
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (workspace["id"], "code-default", 2),
        )
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (
                workspace["id"],
                "education_video_problems_generation",
                "intake_knowledge_points",
                "code-default",
            ),
        )
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="fraction-addition-subtraction",
        batch_id="",
        title="fraction-addition-subtraction",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )

    processed = worker._poll()

    assert processed is True
    assert worker._futures
    for future in worker._futures.values():
        future.result(timeout=5)

    node = queries.get_job_node(job["id"], "intake_knowledge_points")
    assert node is not None
    assert node["status"] == "completed"
    assert (tmp_path / job["storage_dir"] / "knowledge_point.json").exists()

    worker.stop()


def test_worker_uses_job_snapshot_definition_instead_of_catalog_definition(
    tmp_path: Path,
) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code-default")

    v1_nodes = [
        _local_node("fetch_questions", outputs=["questions.json"]),
        WorkflowNode(
            key="clean_and_parse",
            label="clean_and_parse",
            capability="clean_and_parse",
            after=["fetch_questions"],
            inputs=["questions.json"],
            outputs=["questions_parsed.json"],
        ),
        WorkflowNode(
            key="generate_key_info",
            label="generate_key_info",
            capability="generate_key_info",
            after=["clean_and_parse"],
            inputs=["questions_parsed.json"],
            outputs=["key_info_raw.json"],
        ),
    ]
    v1_definition = WorkflowDefinition(
        key="education_video_problems_generation",
        label="Question Comprehension V1",
        intake=WorkflowIntake(),
        nodes={n.key: n for n in v1_nodes},
    )
    v2_nodes = list(v1_nodes) + [
        WorkflowNode(
            key="classify_comprehension_eligibility",
            label="classify",
            capability="classify_comprehension_eligibility",
            inputs=["questions_parsed.json"],
            outputs=["comprehension_eligibility.json"],
        ),
    ]
    v2_definition = WorkflowDefinition(
        key="education_video_problems_generation",
        label="Question Comprehension V2",
        intake=WorkflowIntake(),
        nodes={n.key: n for n in v2_nodes},
        edges=[],
        schema_version=2,
    )

    job = job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=list(v1_definition.nodes),
        workspace_id=ws["id"],
        workflow_revision_id=f"{ws['id']}:education_video_problems_generation:v1",
        workflow_definition_hash="hash-v1",
        workflow_definition_snapshot_json=serialize_definition(v1_definition),
    )
    with job_db.connect() as conn:
        for node in v1_definition.nodes.values():
            conn.execute(
                "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
                (ws["id"], "education_video_problems_generation", node.key, "code-default"),
            )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [v2_definition])
    worker._poll()

    statuses = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert "classify_comprehension_eligibility" not in statuses
    assert statuses["fetch_questions"] in {"running", "completed"}

    executor.block_event.set()
    worker.stop()


def test_ensure_pools_reconciles_with_reloaded_registry(tmp_path: Path) -> None:
    """Executor publish hot reload: pools follow the swapped registry state."""
    from server.app.executors.config import CodeCapabilityConfig, CodeExecutorConfig
    from server.app.executors.kinds import RuntimeDependencies

    db_path = TEST_DATABASE_URL
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])
    worker._poll()
    assert worker._pools["code-default"]._max_workers == 2

    # Simulate a publish hot reload on the shared registry object.
    definitions = {
        "code-default": CodeExecutorConfig(
            kind="code",
            global_capacity=4,
            capabilities={"fetch": CodeCapabilityConfig(path="workflow_nodes/question_intake.py")},
        ),
        "code-extra": CodeExecutorConfig(
            kind="code",
            global_capacity=1,
            capabilities={
                "other": CodeCapabilityConfig(path="workflow_nodes/question_clean_parse.py")
            },
        ),
    }
    worker.registry._runtime = RuntimeDependencies()
    worker.registry.replace_definitions(definitions)
    worker._ensure_pools()

    assert worker._pools["code-default"]._max_workers == 4
    assert worker._pools["code-extra"]._max_workers == 1

    # The archive path: a removed executor loses its pool.
    worker.registry.replace_definitions({"code-extra": definitions["code-extra"]})
    worker._ensure_pools()

    assert "code-default" not in worker._pools
    assert "code-extra" in worker._pools
    worker.stop()


def test_ensure_pools_drop_finishes_unstarted_claims(tmp_path: Path) -> None:
    """Archive hot reload: claimed-but-never-started leases fail fast.

    A queued future cancelled by pool.shutdown(cancel_futures=True) never runs
    run_claim, so without the explicit finish its lease would read as running
    until the TTL sweeper reaps it.
    """
    from server.app.executors.kinds import RuntimeDependencies

    db_path = TEST_DATABASE_URL
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])
    worker._ensure_pools()

    finished: list[tuple[str, object]] = []

    class _RecordingLeases:
        def finish(self, lease_id: str, result: object) -> bool:
            finished.append((lease_id, result))
            return True

    worker.leases = _RecordingLeases()
    pool = worker._pools["code-default"]
    blocker = threading.Event()
    running = [pool.submit(blocker.wait) for _ in range(2)]  # fill both workers
    queued = pool.submit(lambda: None)  # stays queued behind the blockers
    worker._futures["exec-queued"] = queued
    worker._future_claims["exec-queued"] = ("code-default", "lease-queued")
    worker._futures["exec-running"] = running[0]
    worker._future_claims["exec-running"] = ("code-default", "lease-running")

    worker.registry._runtime = RuntimeDependencies()
    worker.registry.replace_definitions({})  # archive: no executor remains
    try:
        worker._ensure_pools()

        assert queued.cancelled()
        assert [lease_id for lease_id, _ in finished] == ["lease-queued"]
        result = finished[0][1]
        assert result.status == "failed"
        assert "archived" in result.error_message
        assert "exec-queued" not in worker._futures
        assert "exec-queued" not in worker._future_claims
        # A future that already started runs to completion and finishes its
        # own lease through run_claim — the drop must not double-finish it.
        assert "exec-running" in worker._futures
    finally:
        worker._futures.pop("exec-running", None)
        worker._future_claims.pop("exec-running", None)
        blocker.set()
        worker.stop()
