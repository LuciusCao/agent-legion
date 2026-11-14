from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.jobs.workflow_upgrade_mutation_inherit import upgrade_job_workflow_inherit
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.storage_paths import resolve_job_dir
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflows.capacity_metadata import node_capacity_requirements
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowNode,
)
from tests.fakes.storage import FakeObjectStorage
from tests.helpers.executor_worker import local_node, make_worker
from tests.helpers.ready_gate_hydration import A_PAYLOAD
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


class _BlockingExecutor:
    kind = "code"

    def __init__(self) -> None:
        self.id = "code"
        self.block_event = threading.Event()

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        assert self.block_event.wait(timeout=10)
        for output in context.expected_outputs:
            (context.job_dir / output).write_text("{}", encoding="utf-8")
        return ExecutionResult(status="completed", exit_code=0)

    def cancel(self, execution_id: str) -> None:
        pass


def _definition(
    *,
    key: str = "test",
    gates: tuple[str, ...] = ("gate",),
    gate_inputs: list[str] | None = None,
    condition: WorkflowCondition | None = None,
    serial_gates: bool = False,
) -> WorkflowDefinition:
    nodes = {
        "run": local_node("run", ["run.json"]),
        "other": local_node("other"),
        **{
            gate: WorkflowNode(
                key=gate,
                label=gate,
                capability="",
                node_type="approval",
                inputs=list(gate_inputs or []),
            )
            for gate in gates
        },
    }
    return WorkflowDefinition(
        key=key,
        label=key,
        intake=WorkflowIntake(),
        nodes=nodes,
        edges=(
            [
                WorkflowEdge(source="run" if index == 0 else gates[index - 1], target=gate)
                for index, gate in enumerate(gates)
            ]
            if serial_gates
            else [WorkflowEdge(source="run", target=gate, condition=condition) for gate in gates]
        ),
    )


def _create_job(
    job_db: JobQueries,
    definition: WorkflowDefinition,
    source_id: str,
    *,
    upstream_status: str = "pending",
) -> dict[str, Any]:
    snapshot = serialize_definition(definition)
    job = job_db.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id=source_id,
        run_id="",
        title=source_id,
        node_keys=list(definition.executable_nodes),
        workspace_id=definition.key,
        workflow_definition_hash=definition_hash(snapshot),
        workflow_definition_snapshot_json=snapshot,
        needs_capacity_by_node=node_capacity_requirements(definition),
    )
    if upstream_status != "pending":
        job_db.update_job_node(str(job["id"]), "run", status=upstream_status)
    return job


def _worker(
    tmp_path: Path,
    definition: WorkflowDefinition,
    *,
    capacity: int = 0,
    executor: Any = None,
):
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    job_db.create_workspace("Test", workspace_id=definition.key)
    return make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        executor or _BlockingExecutor(),
        [definition],
        code_capacity=capacity,
    )


def _status(worker: Any, job_id: str, node_key: str) -> str:
    row = worker.job_db.get_job_node(job_id, node_key)
    assert row is not None
    return str(row["status"])


def _no_full_scan(monkeypatch: pytest.MonkeyPatch, worker: Any) -> None:
    calls = {"count": 0}
    original = worker._runnable_workspaces

    def spy():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(worker, "_runnable_workspaces", spy)
    worker._test_scan_calls = calls


def _assert_no_full_scan(worker: Any) -> None:
    assert worker._test_scan_calls["count"] == 0


def test_saturated_code_pool_parks_ready_approval_without_claiming_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition()
    executor = _BlockingExecutor()
    worker = _worker(tmp_path, definition, capacity=1, executor=executor)
    busy = _create_job(worker.job_db, definition, "busy")

    assert worker._poll() is True
    assert worker.leases.active_counts("code")["global"] == 1
    approval = _create_job(worker.job_db, definition, "approval", upstream_status="completed")
    _no_full_scan(monkeypatch, worker)

    assert worker._poll() is True

    job_id = str(approval["id"])
    assert _status(worker, job_id, "gate") == "awaiting_approval"
    assert _status(worker, job_id, "other") == "pending"
    busy_statuses = [row["status"] for row in worker.job_db.list_job_nodes(str(busy["id"]))]
    assert busy_statuses.count("running") == 1
    assert worker.leases.active_counts("code")["global"] == 1
    _assert_no_full_scan(worker)
    cached = worker.state.job_evals[job_id][1]
    assert {candidate.node.key for candidate in cached} == {"gate", "other"}
    with worker.job_db.connect() as conn:
        assert (
            conn.execute(
                "select count(*) as n from node_runs where job_id=%s and node_key='gate'",
                (job_id,),
            ).fetchone()["n"]
            == 0
        )
    executor.block_event.set()
    worker.stop()


def test_pure_remote_zero_workers_parks_only_ready_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition, capacity=0)
    job = _create_job(worker.job_db, definition, "remote", upstream_status="completed")
    _no_full_scan(monkeypatch, worker)

    assert worker._poll() is True

    assert _status(worker, str(job["id"]), "gate") == "awaiting_approval"
    assert _status(worker, str(job["id"]), "other") == "pending"
    assert worker.leases.active_counts("code")["global"] == 0
    _assert_no_full_scan(worker)
    worker.stop()


@pytest.mark.parametrize(
    "case",
    ("upstream_pending", "serial_second", "until_node", "missing_input"),
)
def test_not_ready_approval_never_parks_without_full_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    definition = _definition(
        gates=("gate", "second") if case == "serial_second" else ("gate",),
        gate_inputs=["missing.json"] if case == "missing_input" else [],
        serial_gates=case == "serial_second",
    )
    worker = _worker(tmp_path, definition)
    job = _create_job(
        worker.job_db,
        definition,
        case,
        upstream_status="pending" if case == "upstream_pending" else "completed",
    )
    job_id = str(job["id"])
    if case == "serial_second":
        worker.job_db.update_job_node(job_id, "gate", status="awaiting_approval")
        worker.job_db.update_job_status(job_id, "awaiting_approval")
    if case == "until_node":
        worker.job_db.set_job_execution_mode(job_id, "until_node", target_node_key="run")
    _no_full_scan(monkeypatch, worker)

    assert worker._poll() is False

    target = "second" if case == "serial_second" else "gate"
    assert _status(worker, job_id, target) == "pending"
    _assert_no_full_scan(worker)
    worker.stop()


def test_unselected_conditional_approval_is_not_parked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    condition = WorkflowCondition(artifact="run.json", path="$.selected", equals=True)
    definition = _definition(condition=condition)
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "condition", upstream_status="completed")
    job_dir = resolve_job_dir(job, worker.settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "run.json").write_text('{"selected": false}', encoding="utf-8")
    _no_full_scan(monkeypatch, worker)

    assert worker._poll() is False

    assert _status(worker, str(job["id"]), "gate") == "not_applicable"
    _assert_no_full_scan(worker)
    worker.stop()


def test_hydration_defer_does_not_park_or_cache_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition(gate_inputs=["run.json"])
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "hydration", upstream_status="completed")
    job_id = str(job["id"])
    storage_key = f"jobs/{definition.key}/{job_id}/run.json"
    with worker.job_db.connect() as conn:
        conn.execute(
            "insert into job_artifacts(job_id, node_key, name, storage_key, size_bytes, content_hash)"
            " values (%s, 'run', 'run.json', %s, %s, %s)",
            (job_id, storage_key, len(A_PAYLOAD), hashlib.sha256(A_PAYLOAD).hexdigest()),
        )
    worker.artifact_object_store = JobArtifactObjectStore(TEST_DATABASE_URL, FakeObjectStorage())
    _no_full_scan(monkeypatch, worker)

    assert worker._poll() is False

    assert _status(worker, job_id, "gate") == "pending"
    assert job_id not in worker.state.job_evals
    assert worker.state.pass_skips["hydration_deferred"] == 1
    _assert_no_full_scan(worker)
    worker.stop()


def test_parallel_ready_gates_both_park_in_one_pass(tmp_path: Path) -> None:
    definition = _definition(gates=("left_gate", "right_gate"))
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "parallel", upstream_status="completed")

    assert worker._poll() is True

    job_id = str(job["id"])
    assert _status(worker, job_id, "left_gate") == "awaiting_approval"
    assert _status(worker, job_id, "right_gate") == "awaiting_approval"
    assert worker.job_db.get_job(job_id)["status"] == "awaiting_approval"
    worker.stop()


def test_workspace_and_job_pause_defer_then_resume(tmp_path: Path) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "paused", upstream_status="completed")
    job_id = str(job["id"])
    control = WorkspaceWorkerControl()
    worker.workspace_worker_control = control
    control.pause(definition.key)

    assert worker._poll() is False
    assert _status(worker, job_id, "gate") == "pending"
    control.resume(definition.key)
    assert worker._poll() is True
    assert _status(worker, job_id, "gate") == "awaiting_approval"

    second = _create_job(worker.job_db, definition, "job-paused", upstream_status="completed")
    second_id = str(second["id"])
    assert worker.job_db.mark_execution_paused(second_id, "operator") is True
    assert worker._poll() is False
    assert _status(worker, second_id, "gate") == "pending"
    assert worker.job_db.clear_execution_paused(second_id) is True
    assert worker._poll() is True
    assert _status(worker, second_id, "gate") == "awaiting_approval"
    worker.stop()


def test_workspace_pause_after_evaluation_blocks_park(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "pause-race", upstream_status="completed")
    control = WorkspaceWorkerControl()
    control.resume(definition.key)
    worker.workspace_worker_control = control
    from server.app.workflow_worker import approval_park

    original = approval_park.collect_ready_candidates
    paused = False

    def pause_after_evaluation(worker_arg, jobs):
        candidates = original(worker_arg, jobs)
        nonlocal paused
        if not paused:
            paused = True
            control.pause(definition.key)
        return candidates

    monkeypatch.setattr(approval_park, "collect_ready_candidates", pause_after_evaluation)

    assert worker._poll() is False
    assert paused is True
    assert _status(worker, str(job["id"]), "gate") == "pending"
    control.resume(definition.key)
    assert worker._poll() is True
    assert _status(worker, str(job["id"]), "gate") == "awaiting_approval"
    worker.stop()


def test_first_park_carries_current_execution_generation(tmp_path: Path) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "first-generation", upstream_status="completed")
    job_id = str(job["id"])
    with worker.job_db.connect() as conn:
        conn.execute("update jobs set execution_generation=1 where id=%s", (job_id,))

    assert worker._poll() is True
    assert _status(worker, job_id, "gate") == "awaiting_approval"
    assert worker.job_db.get_job_node(job_id, "gate")["execution_generation"] == 1
    worker.stop()


def test_job_pause_between_evaluation_and_park_is_rechecked_in_transaction(
    tmp_path: Path,
) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "pause-before-park", upstream_status="completed")
    job_id = str(job["id"])
    original = worker.leases.park_awaiting_approval
    paused = False

    def pause_then_park(job_id_arg: str, node_key: str, **kwargs):
        nonlocal paused
        if not paused:
            paused = worker.job_db.mark_execution_paused(job_id_arg, "operator") is True
            assert paused is True
        return original(job_id_arg, node_key, **kwargs)

    worker.leases.park_awaiting_approval = pause_then_park

    assert worker._poll() is False
    assert worker.job_db.get_job(job_id)["execution_paused"] == 1
    assert _status(worker, job_id, "gate") == "pending"
    assert worker.job_db.clear_execution_paused(job_id) is True
    assert worker._poll() is True
    assert _status(worker, job_id, "gate") == "awaiting_approval"
    worker.stop()


def test_until_node_change_between_evaluation_and_park_is_rechecked_in_transaction(
    tmp_path: Path,
) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "until-before-park", upstream_status="completed")
    job_id = str(job["id"])
    original = worker.leases.park_awaiting_approval
    changed = False

    def retarget_then_park(job_id_arg: str, node_key: str, **kwargs):
        nonlocal changed
        if not changed:
            worker.job_db.set_job_execution_mode(job_id_arg, "until_node", target_node_key="run")
            changed = True
        return original(job_id_arg, node_key, **kwargs)

    worker.leases.park_awaiting_approval = retarget_then_park

    assert worker._poll() is False
    assert changed is True
    assert _status(worker, job_id, "gate") == "pending"
    worker.job_db.clear_job_execution_target(job_id)
    assert worker._poll() is True
    assert _status(worker, job_id, "gate") == "awaiting_approval"
    worker.stop()


def test_terminal_cache_cleanup_retains_active_full_evaluation(tmp_path: Path) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition)
    terminal = _create_job(worker.job_db, definition, "terminal", upstream_status="completed")
    active = _create_job(worker.job_db, definition, "active", upstream_status="completed")
    terminal_id = str(terminal["id"])
    active_id = str(active["id"])

    assert worker._poll() is True
    active_evaluation = worker.state.job_evals[active_id]
    assert terminal_id in worker.state.job_evals
    worker.job_db.update_job_status(terminal_id, "completed")

    assert worker._poll() is False

    assert terminal_id not in worker.state.job_evals
    assert worker.state.job_evals[active_id] == active_evaluation
    assert {candidate.node.key for candidate in active_evaluation[1]} == {"gate", "other"}
    worker.stop()


def test_upgrade_between_evaluation_and_park_rejects_old_generation(tmp_path: Path) -> None:
    definition = _definition()
    worker = _worker(tmp_path, definition)
    job = _create_job(worker.job_db, definition, "generation", upstream_status="completed")
    job_id = str(job["id"])
    snapshot = str(job["workflow_definition_snapshot_json"])
    original = worker.leases.park_awaiting_approval
    upgraded = False

    def upgrade_then_park(job_id_arg: str, node_key: str, **kwargs):
        nonlocal upgraded
        if not upgraded:
            upgraded = True
            with worker.job_db.lease_guarded_mutation(
                job_id_arg, datetime.now(UTC), reject_running_nodes=True
            ) as conn:
                upgrade_job_workflow_inherit(
                    conn,
                    job_id_arg,
                    workflow_revision_id="rev-2",
                    workflow_version=2,
                    workflow_definition_hash=definition_hash(snapshot),
                    workflow_definition_snapshot_json=snapshot,
                    node_keys=list(definition.executable_nodes),
                )
        return original(job_id_arg, node_key, **kwargs)

    worker.leases.park_awaiting_approval = upgrade_then_park

    assert worker._poll() is False
    assert upgraded is True
    assert _status(worker, job_id, "gate") == "pending"
    assert worker.job_db.get_job(job_id)["execution_generation"] == 1
    worker.job_db.update_job_node(job_id, "run", status="completed")
    assert worker._poll() is True
    assert _status(worker, job_id, "gate") == "awaiting_approval"
    worker.stop()


def test_two_workers_park_same_candidate_idempotently(tmp_path: Path) -> None:
    definition = _definition()
    first = _worker(tmp_path, definition)
    job = _create_job(first.job_db, definition, "duplicate", upstream_status="completed")
    second = make_worker(
        tmp_path,
        TEST_DATABASE_URL,
        _BlockingExecutor(),
        [definition],
        code_capacity=0,
    )
    barrier = threading.Barrier(2)
    outcomes: list[Exception | None] = [None, None]

    def run(index: int, worker: Any) -> None:
        try:
            barrier.wait(timeout=10)
            worker._poll()
        except Exception as exc:
            outcomes[index] = exc

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate((first, second))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert outcomes == [None, None]
    assert _status(first, str(job["id"]), "gate") == "awaiting_approval"
    first.stop()
    second.stop()


def test_approval_candidate_query_uses_partial_index(job_db: JobQueries) -> None:
    from server.app.jobs.queries.job_approval_park import APPROVAL_PARK_CANDIDATES_SQL

    with job_db.connect() as conn:
        conn.execute("set local enable_seqscan=off")
        rows = conn.execute(f"explain {APPROVAL_PARK_CANDIDATES_SQL}").fetchall()
    plan = "\n".join(str(row[0]) for row in rows)
    assert "idx_job_nodes_approval_park_candidates" in plan
    assert "Seq Scan on job_nodes" not in plan
