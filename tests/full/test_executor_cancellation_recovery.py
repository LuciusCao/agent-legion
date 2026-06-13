from __future__ import annotations

import multiprocessing
import time
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.config import (
    LocalCapabilityConfig,
    LocalExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.local import LocalExecutor
from server.app.executors.pi import PiExecutor
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.definition import PipelineNode
from server.app.pipelines.pi_runner import PiConfig
from server.app.settings import Settings
from tests.helpers.executor_worker import allocate, bind, make_definition

GRACE = 0.5


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
    skill_dir = skill_root / "reading_analysis" / "blocked_pi"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "references" / "output-contract.md").write_text("contract", encoding="utf-8")
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts" / "validate_output.py").write_text(
        "#!/usr/bin/env python3\nimport sys\n", encoding="utf-8"
    )
    return PiExecutor(
        "pi-default",
        PiConfig(binary=str(fake_pi), cancellation_grace_seconds=GRACE),
        skill_root,
        {"blocked_pi": PiCapabilityConfig(skill="reading_analysis/blocked_pi")},
    )


def _make_registry(local_executor: LocalExecutor, pi_executor: PiExecutor) -> ExecutorRegistry:
    return ExecutorRegistry(
        executors={"local-default": local_executor, "pi-default": pi_executor},
        global_capacities={"local-default": 3, "pi-default": 1},
        definitions={
            "local-default": LocalExecutorConfig(
                kind="local",
                global_capacity=3,
                capabilities={
                    "cooperative": LocalCapabilityConfig(
                        handler="tests.full.test_executor_cancellation_recovery.cooperative_handler"
                    ),
                    "blocked": LocalCapabilityConfig(
                        handler="tests.full.test_executor_cancellation_recovery.blocked_handler"
                    ),
                },
            ),
            "pi-default": PiExecutorConfig(
                kind="pi",
                global_capacity=1,
                capabilities={
                    "blocked_pi": PiCapabilityConfig(skill="reading_analysis/blocked_pi")
                },
            ),
        },
    )


def _make_nodes() -> list[PipelineNode]:
    return [
        PipelineNode(
            key="cooperative",
            label="Cooperative",
            capability="cooperative",
            outputs=["output.json"],
        ),
        PipelineNode(
            key="blocked",
            label="Blocked",
            capability="blocked",
            outputs=["output.json"],
        ),
        PipelineNode(
            key="blocked_pi",
            label="Blocked Pi",
            capability="blocked_pi",
            outputs=["output.json"],
        ),
    ]


@pytest.mark.full_gate
def test_worker_cancellation_recovery_releases_capacity(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Cancel Recovery")

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
        pipeline_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=list(definition.nodes),
        workspace_id=ws["id"],
    )

    leases = ExecutorLeaseRepository(db_path)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=0.1,
        lease_ttl_seconds=2,
        cancellation_grace_seconds=GRACE,
    )
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
        executor_definitions=registry.definitions(),
        executor_runtime=ExecutorRuntimeConfig.model_validate(
            {"pipelines": {"enabled": True}, "openclaw": {"command_template": ["openclaw"]}}
        ),
    )
    worker = PipelineWorkerThread(
        job_db=job_db,
        leases=leases,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )

    worker.start()
    worker._definitions = [definition]
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            local_counts = leases.active_counts("local-default")
            pi_counts = leases.active_counts("pi-default")
            if local_counts.get("global", 0) >= 2 and pi_counts.get("global", 0) >= 1:
                break
            time.sleep(0.05)

        local_counts = leases.active_counts("local-default")
        pi_counts = leases.active_counts("pi-default")
        assert local_counts.get("global", 0) >= 2
        assert pi_counts.get("global", 0) == 1

        # Force lease loss: heartbeats will report inactive, triggering cancellation.
        leases.heartbeat = lambda lease_id, ttl_seconds: False  # type: ignore[method-assign]

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if leases.active_counts("local-default").get("global", 0) == 0:
                break
            time.sleep(0.05)
    finally:
        start_stop = time.monotonic()
        worker.stop(timeout=GRACE + 1)
        stop_elapsed = time.monotonic() - start_stop

    assert stop_elapsed < GRACE + 2, "worker shutdown was unbounded"

    # All leases finalized and capacity released.
    assert leases.active_counts("local-default").get("global", 0) == 0
    assert leases.active_counts("pi-default").get("global", 0) == 0

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
        pipeline_key="test",
        source_type="question",
        source_id="Q2",
        batch_id="",
        title="Q2",
        node_keys=["cooperative"],
        workspace_id=ws["id"],
    )

    leases2 = ExecutorLeaseRepository(db_path)
    runtime2 = ExecutionRuntime(
        leases=leases2,
        registry=registry,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
        cancellation_grace_seconds=GRACE,
    )
    settings2 = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
        executor_definitions=registry.definitions(),
        executor_runtime=ExecutorRuntimeConfig.model_validate(
            {"pipelines": {"enabled": True}, "openclaw": {"command_template": ["openclaw"]}}
        ),
    )
    worker2 = PipelineWorkerThread(
        job_db=job_db,
        leases=leases2,
        registry=registry,
        runtime=runtime2,
        settings=settings2,
    )
    worker2._definitions = [definition]
    worker2._poll()
    assert leases2.active_counts("local-default").get("global", 0) == 1
    for future in list(worker2._futures.values()):
        future.result(timeout=10)
    node2 = job_db.get_job_node(job2["id"], "cooperative")
    assert node2["status"] == "completed"
    assert (Path(job2["storage_dir"]) / "output.json").is_file()
    worker2.stop()
