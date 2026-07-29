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
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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


def test_make_workflow_worker_runs_question_comprehension_info_local_node(
    tmp_path: Path,
) -> None:
    # fetch_questions runs on the code-default executor in an isolated child
    # process, which does not inherit parent monkeypatches. make_workflow_worker
    # injects empty resource declarations, so the node resolves no CMS api_url
    # and writes the base payload without any network call.
    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    worker, definition = make_workflow_worker(tmp_path, queries)
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="question_comprehension_info"
    )
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (workspace["id"], "code-default", 2),
        )
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (workspace["id"], "question_comprehension_info", "fetch_questions", "code-default"),
        )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
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


def test_worker_uses_job_snapshot_definition_instead_of_catalog_definition(
    tmp_path: Path,
) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="question_comprehension_info")
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
        key="question_comprehension_info",
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
        key="question_comprehension_info",
        label="Question Comprehension V2",
        intake=WorkflowIntake(),
        nodes={n.key: n for n in v2_nodes},
        edges=[],
        schema_version=2,
    )

    job = job_db.create_job(
        workflow_key="question_comprehension_info",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=list(v1_definition.nodes),
        workspace_id=ws["id"],
        workflow_revision_id=f"{ws['id']}:question_comprehension_info:v1",
        workflow_definition_hash="hash-v1",
        workflow_definition_snapshot_json=serialize_definition(v1_definition),
    )
    with job_db.connect() as conn:
        for node in v1_definition.nodes.values():
            conn.execute(
                "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
                (ws["id"], "question_comprehension_info", node.key, "code-default"),
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
