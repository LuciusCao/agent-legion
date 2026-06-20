from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from server.app.jobs import JobQueries
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.execution_control import allowed_nodes
from tests.helpers import make_workflow_worker
from tests.workers.helpers import (
    RecordingExecutor,
    _local_node,
    _make_definition,
    _make_worker,
)


def test_worker_creates_shared_pool_per_executor_id(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    executor = RecordingExecutor("local-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()

    assert "local-default" in worker._pools
    assert worker._pools["local-default"]._max_workers == 2
    # No per-workspace pools should exist
    assert not hasattr(worker, "_ws_local_executors") or not worker._ws_local_executors
    assert not hasattr(worker, "_ws_agent_executors") or not worker._ws_agent_executors

    worker.stop()


def test_poll_submits_ready_local_node(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
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
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is True
    assert worker.leases.active_counts("local-default").get("global", 0) == 1
    assert len(worker._futures) == 1

    executor.block_event.set()
    worker.stop()


def test_poll_skips_duplicate_submissions(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    block_event = threading.Event()
    executor = RecordingExecutor("local-default", block_event=block_event)
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
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()
    assert worker.leases.active_counts("local-default").get("global", 0) == 1

    worker._poll()
    assert worker.leases.active_counts("local-default").get("global", 0) == 1

    block_event.set()
    worker.stop()


def test_poll_skips_paused_workspace(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
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
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    control = MagicMock()
    control.is_paused = lambda ws_id: ws_id == ws["id"]
    worker.workspace_worker_control = control

    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("local-default").get("global", 0) == 0

    worker.stop()


def test_poll_fails_node_without_binding(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
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
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node is not None
    assert node["status"] == "failed"
    assert "No Executor binding" in node["error_message"]

    worker.stop()


def test_poll_fails_node_with_unsupported_capability(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
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
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node is not None
    assert node["status"] == "failed"
    assert "does not support capability" in node["error_message"]

    worker.stop()


def test_stop_shuts_down_shared_pools(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    executor = RecordingExecutor("local-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()
    pool = worker._pools["local-default"]
    worker.stop()

    assert pool._shutdown is True


def test_poll_skips_paused_job(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
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
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("local-default").get("global", 0) == 0
    assert len(worker._futures) == 0

    worker.stop()


def test_poll_runs_only_target_closure_in_until_node_mode(tmp_path: Path) -> None:

    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
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
                "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
                (ws["id"], "test", node.key, "local-default"),
            )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
            (ws["id"], "test", "root", 1),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
            (ws["id"], "test", "left", 1),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
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


def test_make_workflow_worker_runs_reading_analysis_local_node(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "server.app.workflows.reading_analysis.get_token",
        lambda env, config: "test-token",
    )
    monkeypatch.setattr(
        "server.app.workflows.reading_analysis.fetch_question_detail",
        lambda question_id, api_url, token: SimpleNamespace(
            question_id=question_id,
            title="Question Q100",
            normalized={},
            payload={"uuid": question_id},
        ),
    )
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, definition = make_workflow_worker(tmp_path, queries)
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    processed = worker._poll()

    assert processed is True
    assert worker._futures
    for future in worker._futures.values():
        future.result(timeout=5)

    node = queries.get_job_node(job["id"], "fetch_questions")
    assert node is not None
    assert node["status"] == "completed"
    assert (tmp_path / job["storage_dir"] / "questions.json").exists()

    worker.stop()
