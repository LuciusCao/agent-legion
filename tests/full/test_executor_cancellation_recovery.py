from __future__ import annotations

import multiprocessing
import time
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.config import PiCapabilityConfig
from server.app.executors.local import LocalExecutor
from server.app.executors.pi import PiExecutor
from server.app.jobs import JobQueries
from server.app.skills.manager import SkillManager
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.pi_runner import PiConfig
from tests.helpers.executor_worker import (
    allocate,
    bind,
    local_node,
    make_definition,
    make_pi_skill,
    make_registry,
    make_worker,
)

GRACE = 0.5


class _StubSkillManager(SkillManager):
    """SkillManager stub that returns an existing on-disk skill directory."""

    def __init__(self, base_dir: Path) -> None:
        super().__init__(
            config_path=base_dir / "skills.yaml",
            lock_path=base_dir / "skills.lock",
            base_dir=base_dir,
        )

    def get_skill_dir(self, skill_key: str, execution_id: str) -> Path:
        return self.base_dir / skill_key


# Repository-owned style handlers, referenced by fully-qualified module path so
# the isolated multiprocessing child can resolve them after spawn.


def cooperative_handler(
    _job: dict[str, Any], artifact_dir: Path, runtime: dict[str, Any] | None
) -> None:
    runtime = runtime or {}
    token = runtime.get("cancellation")
    for _ in range(50):
        if token is not None:
            token.raise_if_cancelled()
        time.sleep(0.01)
    (artifact_dir / "output.json").write_text("{}", encoding="utf-8")


def blocked_handler(
    _job: dict[str, Any], _artifact_dir: Path, _runtime: dict[str, Any] | None
) -> None:
    while True:
        time.sleep(10)


def _local_executor() -> LocalExecutor:
    return LocalExecutor(
        "local-default",
        {
            "cooperative": cooperative_handler,
            "blocked": blocked_handler,
        },
        cancellation_grace_seconds=GRACE,
    )


def _pi_executor(fake_pi: Path, skill_root: Path) -> PiExecutor:
    make_pi_skill(skill_root, "question_comprehension_info/blocked_pi")
    return PiExecutor(
        "pi-default",
        PiConfig(binary=str(fake_pi), cancellation_grace_seconds=GRACE),
        _StubSkillManager(skill_root),
        {"blocked_pi": PiCapabilityConfig(skill="question_comprehension_info/blocked_pi")},
    )


def _make_registry(local_executor: LocalExecutor, pi_executor: PiExecutor) -> Any:
    return make_registry(
        executors={"local-default": local_executor, "pi-default": pi_executor},
        definitions={
            "local-default": {
                "kind": "local",
                "global_capacity": 3,
                "capabilities": {
                    "cooperative": {
                        "handler": "tests.full.test_executor_cancellation_recovery.cooperative_handler"
                    },
                    "blocked": {
                        "handler": "tests.full.test_executor_cancellation_recovery.blocked_handler"
                    },
                },
            },
            "pi-default": {
                "kind": "pi",
                "global_capacity": 1,
                "capabilities": {"blocked_pi": {"skill": "question_comprehension_info/blocked_pi"}},
            },
        },
    )


def _make_nodes() -> list[Any]:
    return [
        local_node("cooperative"),
        local_node("blocked"),
        local_node("blocked_pi"),
    ]


@pytest.mark.slow
@pytest.mark.full_gate
def test_worker_cancellation_recovery_releases_capacity(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "Cancel Recovery", default_workflow_key="question_comprehension_info"
    )

    fake_pi = tmp_path / "fake_pi"
    fake_pi.write_text("#!/bin/bash\ntrap '' TERM\nsleep 1000\n")
    fake_pi.chmod(0o755)

    local_executor = _local_executor()
    pi_executor = _pi_executor(fake_pi, tmp_path / "skills")
    registry = _make_registry(local_executor, pi_executor)
    definition = make_definition(_make_nodes())

    allocate(job_db, ws["id"], "local-default", 3)
    allocate(job_db, ws["id"], "pi-default", 1)
    for node in definition.nodes.values():
        bind(
            job_db,
            ws["id"],
            "test",
            node.key,
            "pi-default" if node.key == "blocked_pi" else "local-default",
        )

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
        registry,
        [],
        heartbeat_interval_seconds=0.1,
        lease_ttl_seconds=2,
        cancellation_grace_seconds=GRACE,
    )

    worker.start()
    worker._definitions = [definition]
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            local_counts = worker.leases.active_counts("local-default")
            pi_counts = worker.leases.active_counts("pi-default")
            if local_counts.get("global", 0) >= 2 and pi_counts.get("global", 0) >= 1:
                break
            time.sleep(0.05)

        local_counts = worker.leases.active_counts("local-default")
        pi_counts = worker.leases.active_counts("pi-default")
        assert local_counts.get("global", 0) >= 2
        assert pi_counts.get("global", 0) == 1

        # Force lease loss: heartbeats will report inactive, triggering cancellation.
        worker.leases.heartbeat = lambda lease_id, ttl_seconds: False  # type: ignore[method-assign]

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if worker.leases.active_counts("local-default").get("global", 0) == 0:
                break
            time.sleep(0.05)
    finally:
        start_stop = time.monotonic()
        worker.stop(timeout=GRACE + 1)
        stop_elapsed = time.monotonic() - start_stop

    assert stop_elapsed < GRACE + 2, "worker shutdown was unbounded"

    # All leases finalized and capacity released.
    assert worker.leases.active_counts("local-default").get("global", 0) == 0
    assert worker.leases.active_counts("pi-default").get("global", 0) == 0

    # Active process maps drained.
    assert pi_executor._tracker.active() == []
    assert not multiprocessing.active_children()

    # Each execution finalized exactly once.
    with job_db.connect() as conn:
        finished_runs = conn.execute(
            "select count(*) from node_runs where job_id=? and status='failed'",
            (job["id"],),
        ).fetchone()[0]
    assert finished_runs == 3

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
        registry,
        [definition],
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
        cancellation_grace_seconds=GRACE,
    )
    worker2._poll()
    assert worker2.leases.active_counts("local-default").get("global", 0) == 1
    for future in list(worker2._futures.values()):
        future.result(timeout=10)
    node2 = job_db.get_job_node(job2["id"], "cooperative")
    assert node2["status"] == "completed"
    assert (resolve_job_dir(job2, tmp_path / "jobs") / "output.json").is_file()
    worker2.stop()
