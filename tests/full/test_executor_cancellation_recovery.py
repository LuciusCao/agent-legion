from __future__ import annotations

import multiprocessing
import time
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.code import CodeExecutor
from server.app.jobs import JobQueries
from server.app.storage_paths import resolve_job_dir
from tests.helpers.executor_worker import (
    local_node,
    make_definition,
    make_worker,
)
from tests.postgres_support import TEST_DATABASE_URL

GRACE = 0.5


# Minimal code-node sources, published as workspace node codes and executed
# from the text inside the velites sandbox (post-#96; the path-loaded bare
# child is gone).

_COOPERATIVE_NODE = """\
import time


def run(job, job_dir, runtime):
    runtime = runtime or {}
    token = runtime.get("cancellation")
    for _ in range(50):
        if token is not None:
            token.raise_if_cancelled()
        time.sleep(0.01)
    (job_dir / "output.json").write_text("{}", encoding="utf-8")
"""

_BLOCKED_NODE = """\
import time


def run(job, job_dir, runtime):
    while True:
        time.sleep(10)
"""


def _local_executor(repo_root: Path) -> CodeExecutor:
    return CodeExecutor(
        repo_root=repo_root,
        cancellation_grace_seconds=GRACE,
    )


def _make_nodes() -> list[Any]:
    return [
        local_node("cooperative"),
        local_node("blocked"),
    ]


@pytest.mark.slow
@pytest.mark.full_gate
def test_worker_cancellation_recovery_releases_capacity(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Cancel Recovery", default_workflow_key="demo_workflow")

    from tests.executors.test_code_executor import _sandbox_backend_available, _velites_binary

    if not _sandbox_backend_available():
        pytest.skip("no OS sandbox backend (macOS sandbox-exec / Linux bwrap)")
    binary = _velites_binary()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "server.app.executors._code_sandbox.shutil.which", lambda _name: str(binary)
    )

    local_executor = _local_executor(tmp_path)
    definition = make_definition(_make_nodes())

    from server.app.services.node_codes import NodeCodeService

    codes = NodeCodeService(db_path)
    for node_key, code in (("cooperative", _COOPERATIVE_NODE), ("blocked", _BLOCKED_NODE)):
        codes.save_draft(ws["id"], "test", node_key, code, "test seed")
        codes.publish(ws["id"], "test", node_key)

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=list(definition.nodes),
        workspace_id=ws["id"],
    )

    worker = make_worker(
        tmp_path,
        db_path,
        local_executor,
        [],
        code_capacity=3,
        heartbeat_interval_seconds=0.1,
        lease_ttl_seconds=2,
        cancellation_grace_seconds=GRACE,
    )

    worker.start()
    worker._scan_entries = ([definition], [])
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if worker.leases.active_counts("code").get("global", 0) >= 2:
                break
            time.sleep(0.05)

        assert worker.leases.active_counts("code").get("global", 0) >= 2

        # Force lease loss: heartbeats will report inactive, triggering cancellation.
        worker.leases.heartbeat = lambda lease_id, ttl_seconds: False  # type: ignore[method-assign]

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if worker.leases.active_counts("code").get("global", 0) == 0:
                break
            time.sleep(0.05)
    finally:
        start_stop = time.monotonic()
        worker.stop(timeout=GRACE + 1)
        stop_elapsed = time.monotonic() - start_stop
        monkeypatch.undo()

    assert stop_elapsed < GRACE + 2, "worker shutdown was unbounded"

    # All leases finalized and capacity released.
    assert worker.leases.active_counts("code").get("global", 0) == 0

    # Active process maps drained.
    assert not multiprocessing.active_children()

    # Each execution finalized exactly once.
    with job_db.connect() as conn:
        finished_runs = conn.execute(
            "select count(*) from node_runs where job_id=%s and status='failed'",
            (job["id"],),
        ).fetchone()[0]
    assert finished_runs == 2

    # Capacity reuse: a newly queued cooperative node can be claimed and completed.
    job2 = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q2",
        batch_id="",
        title="Q2",
        node_keys=["cooperative"],
        workspace_id=ws["id"],
    )

    worker2 = make_worker(
        tmp_path,
        db_path,
        local_executor,
        [definition],
        code_capacity=3,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
        cancellation_grace_seconds=GRACE,
    )
    worker2._poll()
    assert worker2.leases.active_counts("code").get("global", 0) == 1
    for future in list(worker2._futures.values()):
        future.result(timeout=10)
    node2 = job_db.get_job_node(job2["id"], "cooperative")
    assert node2["status"] == "completed"
    assert (resolve_job_dir(job2, tmp_path / "jobs") / "output.json").is_file()
    worker2.stop()
