"""Pure-remote mode (#389): code_capacity == 0 assembles no local executor
stack, yet the scheduler keeps running — code nodes ride the remote dispatch
path and nothing falls back to a local pool that does not exist."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.app.configuration.executor_runtime import ExecutorRuntimeConfig
from server.app.executors.models import CODE_EXECUTOR_ID, ExecutionContext, ExecutionResult
from server.app.executors.scheduling.capacity import load_capacity_snapshot
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker.pools import ensure_pools
from tests.helpers.executor_worker import make_definition, make_worker
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


def _settings(tmp_path: Path, code_capacity: int) -> Settings:
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
        database_url=str(tmp_path / "unused"),
        executor_runtime=ExecutorRuntimeConfig.model_validate({"code_capacity": code_capacity}),
    )


def _worker(tmp_path: Path, code_capacity: int) -> MagicMock:
    worker = MagicMock()
    worker.settings = _settings(tmp_path, code_capacity)
    worker.state.pools = {}
    return worker


class ExplodingExecutor:
    """A local executor that must never be reached in pure-remote mode."""

    kind = "code"

    def __init__(self) -> None:
        self.id = CODE_EXECUTOR_ID
        self.reached = False

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.reached = True
        return ExecutionResult(
            status="failed",
            exit_code=1,
            error_message="local executor reached in pure-remote mode",
            log_path=str(context.log_path),
        )

    def cancel(self, execution_id: str) -> None:
        pass


def test_code_capacity_zero_is_accepted_by_both_contracts() -> None:
    from server.app.routes.instance_settings_contracts import InstanceSettingsUpdate

    doc = InstanceSettingsUpdate.model_validate(
        {
            "cleanup": {
                "log_retention_days": 30,
                "run_dir_retention_days": 3,
                "interval_seconds": 3600,
            },
            "monitoring": {"sample_interval_seconds": 15.0, "retention_days": 30},
            "heartbeat_interval_seconds": 10.0,
            "lease_ttl_seconds": 90,
            "heartbeat_failure_threshold": 3,
            "sweeper_enabled": True,
            "sweeper_interval_seconds": 5.0,
            "code_capacity": 0,
            "materials_ttl_days": 0,
            "execution_retention_days": 0,
            "workflows": {"max_items_per_run": 20_000},
            "agent_workers": {"max_archive_bytes": 1024, "min_protocol_version": 1},
        }
    )
    assert doc.code_capacity == 0

    runtime = ExecutorRuntimeConfig.model_validate({"code_capacity": 0})
    assert runtime.code_capacity == 0


def test_pools_not_built_in_pure_remote_mode(tmp_path: Path) -> None:
    worker = _worker(tmp_path, code_capacity=0)
    ensure_pools(worker)
    assert worker.state.pools == {}

    # A pool left behind by a size drift (restart lowered capacity to 0) is
    # torn down rather than kept.
    stale = MagicMock()
    worker.state.pools[CODE_EXECUTOR_ID] = stale
    ensure_pools(worker)
    assert worker.state.pools == {}
    stale.shutdown.assert_called_once()


def test_pools_built_when_capacity_positive(tmp_path: Path) -> None:
    worker = _worker(tmp_path, code_capacity=4)
    ensure_pools(worker)
    assert worker.state.pools[CODE_EXECUTOR_ID]._max_workers == 4


def test_settings_default_code_capacity_stays_positive() -> None:
    # The default stays 16: pure-remote is an explicit operator choice.
    runtime = ExecutorRuntimeConfig()
    assert runtime.code_capacity == 16


def test_negative_code_capacity_still_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExecutorRuntimeConfig.model_validate({"code_capacity": -1})


def test_capacity_snapshot_reports_zero_global_in_pure_remote_mode(
    tmp_path: Path,
) -> None:
    # Any DB serves: the snapshot counts zero active leases either way, and
    # code_capacity=0 keeps global_remaining at 0 while node-level limits
    # still populate (remote code claims are node-counted too).
    snapshot = load_capacity_snapshot(TEST_DATABASE_URL, code_capacity=0)
    assert snapshot.has_any_capacity() is False
    assert snapshot.has_capacity("ws1", "node_a") is False
    # Node ceilings survive independently of the local pool.
    assert all(remaining >= 0 for remaining in snapshot.node_remaining.values())


def test_pure_remote_poll_never_reaches_local_executor(tmp_path: Path) -> None:
    """End-to-end poll in pure-remote mode: a code node with no online remote
    Worker stays pending — it must NOT fall back to a local executor."""
    from server.app.services.workflow_revision_format import definition_hash, serialize_definition
    from server.app.workflows.definition import WorkflowNode

    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace(
        "PureRemote", default_workflow_key="pure_remote", workspace_id="pure_remote"
    )
    definition = make_definition(
        [WorkflowNode(key="solo", label="solo", capability="solo", outputs=["out.json"])]
    )
    snapshot_json = serialize_definition(definition)
    job = job_db.create_job(
        workflow_key="pure_remote",
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["solo"],
        workspace_id=ws["id"],
        workflow_definition_hash=definition_hash(snapshot_json),
        workflow_definition_snapshot_json=snapshot_json,
    )

    executor = ExplodingExecutor()
    worker = make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor,
        [definition],
        code_capacity=0,
    )
    # No remote Worker is online in this test: neuter both dispatch paths.
    worker.code_dispatch = None
    worker.agent_dispatch = None

    for _ in range(3):
        worker._poll()

    assert executor.reached is False, "pure-remote mode must not execute locally"
    node = job_db.get_job_node(job["id"], "solo")
    # The node stays in a runnable state, pending a remote Worker.
    assert node["status"] in ("ready", "pending", "stale")
