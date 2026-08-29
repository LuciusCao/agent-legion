from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
    _seed_trivial_node_code,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_worker_creates_the_single_code_pool(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    executor = RecordingExecutor("code")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()

    # P-0.5: exactly one implicit code pool, sized from code_capacity.
    assert set(worker.state.pools) == {"code"}
    assert worker.state.pools["code"]._max_workers == 2
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
    executor = RecordingExecutor("code")
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    _seed_trivial_node_code(db_path, ws["id"], "test", "fetch")

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is True
    assert worker.leases.active_counts("code").get("global", 0) == 1
    assert len(worker.state.futures) == 1

    executor.block_event.set()
    worker.stop()


def test_poll_skips_duplicate_submissions(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    block_event = threading.Event()
    executor = RecordingExecutor("code", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    _seed_trivial_node_code(db_path, ws["id"], "test", "fetch")

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()
    assert worker.leases.active_counts("code").get("global", 0) == 1

    worker._poll()
    assert worker.leases.active_counts("code").get("global", 0) == 1

    block_event.set()
    worker.stop()


def test_poll_skips_paused_workspace(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code")
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    control = MagicMock()
    control.is_paused = lambda ws_id: ws_id == ws["id"]
    worker.workspace_worker_control = control

    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("code").get("global", 0) == 0

    worker.stop()


def test_poll_fails_node_without_published_code(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node is not None
    assert node["status"] == "failed"
    assert "no published node code" in node["error_message"]

    worker.stop()


def test_stop_shuts_down_shared_pools(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    executor = RecordingExecutor("code")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()
    pool = worker.state.pools["code"]
    worker.stop()

    assert pool._shutdown is True


def test_poll_loop_thread_survives_repeated_poll_failures(tmp_path: Path, caplog) -> None:
    """#204: the poll loop's broad catch is the thread's life support — a
    failing _poll (programming error or infrastructure outage) is logged
    with its traceback and the loop keeps running; the next pass is the
    built-in retry."""
    import logging
    import time

    db_path = TEST_DATABASE_URL
    executor = RecordingExecutor("code")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    calls = {"count": 0}

    def failing_poll():
        calls["count"] += 1
        raise RuntimeError("infra outage")

    real_poll = worker._poll
    worker._poll = failing_poll

    with caplog.at_level(logging.ERROR, logger="server.app.workflow_worker.thread"):
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while calls["count"] < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            worker._poll = real_poll
            worker.stop()

    assert calls["count"] >= 2, "the poll loop died instead of retrying after a failure"
    failures = [rec for rec in caplog.records if "workflow worker poll failed" in rec.message]
    assert len(failures) >= 2
    assert all(rec.exc_info for rec in failures), "poll failures must log the traceback"


def test_poll_skips_paused_job(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    job_db.pause_job(job["id"], "awaiting_resources")

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("code").get("global", 0) == 0
    assert len(worker.state.futures) == 0

    worker.stop()


def test_poll_runs_only_target_closure_in_until_node_mode(tmp_path: Path) -> None:

    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Test WS", default_workflow_key="education_video_problems_generation"
    )
    executor = RecordingExecutor("code")
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
        run_id="",
        title="Q1",
        node_keys=["root", "left", "target", "right"],
        workspace_id=ws["id"],
    )
    job_db.set_job_execution_target(job["id"], "target")
    for key in ("root", "left", "target", "right"):
        _seed_trivial_node_code(db_path, ws["id"], "test", key)
    with job_db.connect() as conn:
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
    # intake_knowledge_points runs on the code-default executor inside the
    # velites sandbox (post-#96: the demo node code is a workspace seed,
    # executed from the DB text): it reads the job's material input — a
    # knowledge-point markdown materialized into the local cache from a fake
    # object store (design §6.2; pure stdlib, no network) — and writes
    # knowledge_point.json for the downstream agent nodes.
    if os.environ.get("GATE_SHARD"):
        pytest.skip("CI hash shard runs this OS sandbox integration in its isolated step")

    import hashlib
    import io
    import json
    import shutil
    import subprocess
    import sys

    if sys.platform == "darwin":
        backend = shutil.which("sandbox-exec")
    elif sys.platform == "linux":
        backend = shutil.which("bwrap")
    else:
        backend = None
    if backend is None:
        pytest.skip("no OS sandbox backend (macOS sandbox-exec / Linux bwrap)")
    velites = REPO_ROOT / "velites" / "target" / "debug" / "velites"
    if not velites.exists():
        cargo = shutil.which("cargo")
        if cargo is None:
            pytest.skip("no prebuilt velites binary and cargo is not available")
        proc = subprocess.run(
            [cargo, "build", "--manifest-path", str(REPO_ROOT / "velites" / "Cargo.toml")],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not velites.exists():
            pytest.skip(f"velites build failed: {proc.stderr[-400:]}")
    monkeypatch.setattr(
        "server.app.executors._code_sandbox.shutil.which", lambda _name: str(velites)
    )

    queries = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    worker, definition = make_workflow_worker(tmp_path, queries)
    workspace = queries.create_workspace(
        "test_ws", default_workflow_key="education_video_problems_generation"
    )
    from server.app.services.demo_node_seed import seed_demo_workspace_node_codes

    seed_demo_workspace_node_codes(worker.settings, workspace["id"])

    # Material input: the repo's sample markdown as a ready materials row plus
    # a fake object store holding its bytes (no real S3 in tests).
    workspace_id = str(workspace["id"])
    filename = "fraction-addition-subtraction.md"
    payload = (
        REPO_ROOT / "examples" / "education-video-problems-generation" / filename
    ).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    storage_key = f"{workspace_id}/{digest}/{filename}"

    class _FakeStorage:
        def open_stream(self, key: str) -> io.BytesIO:
            assert key == storage_key
            return io.BytesIO(payload)

    executor = worker.runtime.executor
    executor._storage_probed = True
    executor._object_storage = _FakeStorage()
    executor._materials_cache_root = tmp_path / "materials_cache"

    with queries.connect() as conn:
        conn.execute(
            "insert into materials("
            " id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by"
            ") values ('mat-demo-1', %s, %s, %s, 'text/markdown', %s, %s, 'ready', 'system')",
            (workspace_id, digest, filename, len(payload), storage_key),
        )
    job = queries.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="fraction-addition-subtraction",
        run_id="",
        title="fraction-addition-subtraction",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
    )
    with queries.connect() as conn:
        conn.execute(
            "update jobs set input_json=%s where id=%s",
            (json.dumps({"type": "material", "material_id": "mat-demo-1"}), job["id"]),
        )

    processed = worker._poll()

    assert processed is True
    assert worker.state.futures
    for future in worker.state.futures.values():
        future.result(timeout=5)

    node = queries.get_job_node(job["id"], "intake_knowledge_points")
    assert node is not None
    log_path = node.get("log_path")
    log = (
        Path(log_path).read_text(encoding="utf-8") if log_path and Path(log_path).is_file() else ""
    )
    discovered_logs = {
        str(path.relative_to(tmp_path)): path.read_text(encoding="utf-8", errors="replace")[-2000:]
        for path in tmp_path.rglob("*.log")
    }
    if node["status"] != "completed":
        print(f"sandbox logs: {discovered_logs}")
    assert node["status"] == "completed", {
        "node": node,
        "log": log[-2000:],
        "discovered_logs": discovered_logs,
    }
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
    executor = RecordingExecutor("code")

    v1_nodes = [
        _local_node("fetch_items", outputs=["questions.json"]),
        WorkflowNode(
            key="clean_items",
            label="clean_items",
            capability="clean_items",
            after=["fetch_items"],
            inputs=["questions.json"],
            outputs=["questions_parsed.json"],
        ),
        WorkflowNode(
            key="generate_key_info",
            label="generate_key_info",
            capability="generate_key_info",
            after=["clean_items"],
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
            key="classify_items",
            label="classify",
            capability="classify_items",
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
        run_id="",
        title="Q1",
        node_keys=list(v1_definition.nodes),
        workspace_id=ws["id"],
        workflow_revision_id=f"{ws['id']}:education_video_problems_generation:v1",
        workflow_definition_hash="hash-v1",
        workflow_definition_snapshot_json=serialize_definition(v1_definition),
    )
    for key in ("fetch_items", "clean_items", "generate_key_info"):
        _seed_trivial_node_code(db_path, ws["id"], "education_video_problems_generation", key)

    worker = _make_worker(tmp_path, db_path, executor, [v2_definition])
    worker._poll()

    statuses = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    assert "classify_items" not in statuses
    assert statuses["fetch_items"] in {"running", "completed"}

    executor.block_event.set()
    worker.stop()
