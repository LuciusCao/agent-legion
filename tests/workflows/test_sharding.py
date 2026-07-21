"""Shard materialization + aggregate state machine + max_concurrency hint.

Covers the Task 7 test matrix: DB-level materialization/aggregation semantics
(items 1-9) and end-to-end scheduling through real leases (items 10-14,
including the EXEC-SHARD-001 evidence assertion).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.schema import WorkflowReduceSpec, WorkflowShardSpec
from server.app.workflows.sharding import (
    ShardLimitExceeded,
    aggregate_shard_state,
    materialize_shards,
    on_shard_finished,
)
from tests.helpers.executor_worker import (
    allocate,
    bind,
    local_def,
    make_definition,
    make_registry,
    make_worker,
)

# ---------------------------------------------------------------------------
# DB-level helpers (unit matrix 1-9)
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with write_transaction(db_path) as conn:
        conn.execute("insert into workspaces(id, name) values ('w1', 'ws')")
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " title, status, storage_dir)"
            " values ('j1','w1','wf','s','s1','t','pending','d')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('j1','review','pending')"
        )
    return db_path


def _shard_statuses(db_path: Path) -> list[str]:
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "select status from node_shards where job_id='j1' and node_key='review'"
            " order by shard_index"
        ).fetchall()
    return [row["status"] for row in rows]


def _aggregate(db_path: Path) -> str:
    with read_connection(db_path) as conn:
        return aggregate_shard_state(conn, "j1", "review")


# ---------------------------------------------------------------------------
# 物化（items 1-3）
# ---------------------------------------------------------------------------


def test_materialize_1000_shards_single_call(tmp_path):
    """Item 1: 1000 条一次物化，返回 1000、行数 1000、全 pending，且足够快。"""
    db_path = _make_db(tmp_path)
    inputs = [{"i": i} for i in range(1000)]
    start = time.monotonic()
    with write_transaction(db_path) as conn:
        inserted = materialize_shards(conn, "j1", "review", inputs, max_shards=1000)
    elapsed = time.monotonic() - start
    assert inserted == 1000
    assert elapsed < 2.0, f"1000 分片物化耗时 {elapsed:.3f}s，可能阻塞 worker tick"
    assert _shard_statuses(db_path) == ["pending"] * 1000


def test_materialize_over_limit_raises_and_writes_nothing(tmp_path):
    """Item 2: 超过 max_shards → ShardLimitExceeded，零行写入。"""
    db_path = _make_db(tmp_path)
    with pytest.raises(ShardLimitExceeded), write_transaction(db_path) as conn:
        materialize_shards(conn, "j1", "review", [{"i": i} for i in range(11)], max_shards=10)
    assert _shard_statuses(db_path) == []


def test_materialize_is_idempotent(tmp_path):
    """Item 3: 重复物化同 (job_id, node_key) 幂等，二次调用返回已存在数。"""
    db_path = _make_db(tmp_path)
    inputs = [{"i": i} for i in range(5)]
    with write_transaction(db_path) as conn:
        assert materialize_shards(conn, "j1", "review", inputs, max_shards=1000) == 5
    with write_transaction(db_path) as conn:
        assert materialize_shards(conn, "j1", "review", inputs, max_shards=1000) == 5
    assert _shard_statuses(db_path) == ["pending"] * 5


# ---------------------------------------------------------------------------
# 聚合状态机（items 4-9）
# ---------------------------------------------------------------------------


def _materialize3(db_path: Path) -> None:
    with write_transaction(db_path) as conn:
        materialize_shards(conn, "j1", "review", [{"i": i} for i in range(3)], max_shards=1000)


def _finish(db_path: Path, index: int, status: str, **kwargs) -> str:
    with write_transaction(db_path) as conn:
        return on_shard_finished(conn, "j1", "review", index, status, **kwargs)


def _start(db_path: Path, index: int) -> None:
    with write_transaction(db_path) as conn:
        conn.execute(
            "update node_shards set status='running', execution_id=?, started_at=current_timestamp"
            " where job_id='j1' and node_key='review' and shard_index=?",
            (f"exec-{index}", index),
        )


def test_aggregate_all_pending(tmp_path):
    """Item 4: 全 pending → aggregate == 'pending'."""
    db_path = _make_db(tmp_path)
    _materialize3(db_path)
    assert _aggregate(db_path) == "pending"


def test_aggregate_any_running(tmp_path):
    """Item 5: 任一 running → 'running'."""
    db_path = _make_db(tmp_path)
    _materialize3(db_path)
    _start(db_path, 1)
    assert _aggregate(db_path) == "running"


def test_aggregate_all_completed(tmp_path):
    """Item 6: 全 completed → 'completed'."""
    db_path = _make_db(tmp_path)
    _materialize3(db_path)
    _start(db_path, 0)
    _start(db_path, 1)
    _start(db_path, 2)
    assert _finish(db_path, 0, "completed", output_json='{"a": 1}') == "running"
    assert _finish(db_path, 1, "completed") == "running"
    assert _finish(db_path, 2, "completed") == "completed"
    assert _aggregate(db_path) == "completed"
    with read_connection(db_path) as conn:
        row = conn.execute(
            "select output_json, finished_at from node_shards"
            " where job_id='j1' and node_key='review' and shard_index=0"
        ).fetchone()
    assert row["output_json"] == '{"a": 1}'
    assert row["finished_at"] is not None


def test_aggregate_any_failed(tmp_path):
    """Item 7: 任一 failed → 'failed'（重试耗尽判定在调用方，本层只记录）。"""
    db_path = _make_db(tmp_path)
    _materialize3(db_path)
    _start(db_path, 2)
    assert _finish(db_path, 2, "failed", error_message="boom") == "failed"
    assert _aggregate(db_path) == "failed"
    with read_connection(db_path) as conn:
        row = conn.execute(
            "select error_message from node_shards"
            " where job_id='j1' and node_key='review' and shard_index=2"
        ).fetchone()
    assert row["error_message"] == "boom"


def test_aggregate_recovers_after_failed_shard_reset(tmp_path):
    """Item 8: failed 分片被重置回 pending（重试）后聚合回到 pending/running。"""
    db_path = _make_db(tmp_path)
    _materialize3(db_path)
    _start(db_path, 0)
    assert _finish(db_path, 0, "failed", error_message="boom") == "failed"
    with write_transaction(db_path) as conn:
        conn.execute(
            "update node_shards set status='pending', execution_id='', error_message='',"
            " started_at=null, finished_at=null"
            " where job_id='j1' and node_key='review' and shard_index=0"
        )
    assert _aggregate(db_path) == "pending"
    _start(db_path, 0)
    assert _aggregate(db_path) == "running"


def test_aggregate_completed_running_mix_is_running(tmp_path):
    """Item 9: completed 与 running 混合 → 'running'."""
    db_path = _make_db(tmp_path)
    _materialize3(db_path)
    _start(db_path, 0)
    _start(db_path, 1)
    assert _finish(db_path, 0, "completed") == "running"
    assert _aggregate(db_path) == "running"


# ---------------------------------------------------------------------------
# 端到端（items 10-14）：fake executor + 真实 leases + 真实 worker
# ---------------------------------------------------------------------------


class FakeShardExecutor:
    """Records contexts; writes parse inputs; returns per-shard output_json."""

    kind = "local"

    def __init__(
        self,
        executor_id: str = "local-default",
        gate: threading.Event | None = None,
        fail_shards: set[int] | None = None,
    ) -> None:
        self.id = executor_id
        self._gate = gate
        self._fail_shards = fail_shards or set()
        self.contexts: list[ExecutionContext] = []
        self.merged_shards: list | None = None
        self._lock = threading.Lock()

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        with self._lock:
            self.contexts.append(context)
        if context.node_key == "parse":
            (context.job_dir / "questions.json").write_text(
                json.dumps([{"q": i} for i in range(4)]), encoding="utf-8"
            )
            return ExecutionResult(status="completed", exit_code=0)
        if context.node_key == "aggregate":
            shards_path = context.job_dir / f"{context.node_key}.shards.json"
            with self._lock:
                self.merged_shards = json.loads(shards_path.read_text(encoding="utf-8"))
            return ExecutionResult(status="completed", exit_code=0)
        shard_index = int(context.runtime["shard_index"])
        if self._gate is not None and not self._gate.wait(timeout=30):
            raise RuntimeError("gate was not released in time")
        if shard_index in self._fail_shards:
            return ExecutionResult(
                status="failed", exit_code=1, error_message=f"shard {shard_index} failed"
            )
        return ExecutionResult(
            status="completed",
            exit_code=0,
            output_json=json.dumps({"shard": shard_index, "input": context.runtime["shard_input"]}),
        )

    def cancel(self, execution_id: str) -> None:
        pass


def _over_definition():
    return make_definition(
        [
            WorkflowNode(
                key="parse", label="parse", capability="parse", outputs=["questions.json"]
            ),
            WorkflowNode(
                key="review",
                label="review",
                capability="review",
                after=["parse"],
                inputs=["questions.json"],
                outputs=["review.json"],
                shard=WorkflowShardSpec(over="inputs.questions.json"),
            ),
            WorkflowNode(
                key="aggregate",
                label="aggregate",
                capability="merge",
                after=["review"],
                outputs=["merged.json"],
                reduce=WorkflowReduceSpec(from_node="review"),
            ),
        ]
    )


def _make_e2e(tmp_path: Path, definition, executor, *, capacity: int = 10):
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    workspace = job_db.create_workspace("ws", default_workflow_key="test")
    capabilities = {node.capability for node in definition.nodes.values()}
    registry = make_registry(
        {"local-default": executor},
        {"local-default": local_def(capacity, capabilities)},
    )
    allocate(job_db, workspace["id"], "local-default", capacity)
    for node in definition.nodes.values():
        bind(job_db, workspace["id"], "test", node.key, "local-default")
    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="src-1",
        batch_id="",
        title="t",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
    )
    worker = make_worker(tmp_path, db_path, registry, [definition])
    job_dir = tmp_path / "jobs" / workspace["id"] / str(job["id"])
    return worker, job_db, job, job_dir


def _poll_until(worker, predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        worker._poll()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _node_status(job_db: JobQueries, job_id: str, node_key: str) -> str | None:
    node = job_db.get_job_node(job_id, node_key)
    return str(node["status"]) if node else None


def _lease_rows(db_path: Path, job_id: str, node_key: str) -> list[dict]:
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "select * from executor_leases where job_id=? and node_key=? order by acquired_at",
            (job_id, node_key),
        ).fetchall()
    return [dict(row) for row in rows]


def _active_lease_count(db_path: Path, job_id: str, node_key: str) -> int:
    with read_connection(db_path) as conn:
        return conn.execute(
            "select count(*) as cnt from executor_leases"
            " where job_id=? and node_key=? and status='active'",
            (job_id, node_key),
        ).fetchone()["cnt"]


def _node_shards(db_path: Path, job_id: str, node_key: str) -> list[dict]:
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "select * from node_shards where job_id=? and node_key=? order by shard_index",
            (job_id, node_key),
        ).fetchall()
    return [dict(row) for row in rows]


def test_shard_node_fans_out_into_independent_leases(tmp_path):
    """Item 10: shard 节点 ready → 物化 → 4 个 shard 各自 try_claim 成功、独立 lease。"""
    executor = FakeShardExecutor()
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor)
    db_path = worker.leases.path
    try:
        ok = _poll_until(worker, lambda: len(_lease_rows(db_path, job["id"], "review")) == 4)
        assert ok, "4 个分片未全部获得独立 lease"
        leases = _lease_rows(db_path, job["id"], "review")
        assert len({row["id"] for row in leases}) == 4
        assert len({row["execution_id"] for row in leases}) == 4
        assert len({row["node_run_id"] for row in leases}) == 4
        shards = _node_shards(db_path, job["id"], "review")
        assert [json.loads(row["input_json"]) for row in shards] == [{"q": i} for i in range(4)]
        assert all(row["execution_id"] for row in shards)
        assert all(row["started_at"] for row in shards)
    finally:
        worker.stop()


def test_all_shards_completed_advances_node_and_reduce(tmp_path):
    """Item 11: 全部 shard completed → job_nodes completed → reduce ready 并聚合输出。"""
    executor = FakeShardExecutor()
    worker, job_db, job, job_dir = _make_e2e(tmp_path, _over_definition(), executor)
    db_path = worker.leases.path
    try:
        ok = _poll_until(worker, lambda: _node_status(job_db, job["id"], "review") == "completed")
        assert ok, "shard 节点未推进到 completed"
        assert all(
            row["status"] == "completed" for row in _node_shards(db_path, job["id"], "review")
        )
        ok = _poll_until(
            worker, lambda: _node_status(job_db, job["id"], "aggregate") == "completed"
        )
        assert ok, "reduce 节点未在 shard 节点完成后 ready"
        expected = [{"shard": i, "input": {"q": i}} for i in range(4)]
        content = json.loads((job_dir / "aggregate.shards.json").read_text(encoding="utf-8"))
        assert content == expected
        assert executor.merged_shards == expected
        reduce_contexts = [c for c in executor.contexts if c.node_key == "aggregate"]
        assert reduce_contexts
        assert "aggregate.shards.json" in reduce_contexts[0].inputs
        assert job_db.get_job(job["id"])["status"] == "completed"
    finally:
        worker.stop()


def test_max_concurrency_hint_limits_claims_per_tick(tmp_path):
    """Item 12: max_concurrency=2、4 分片：同一 tick claim 不超过 2 个。"""
    gate = threading.Event()
    executor = FakeShardExecutor(gate=gate)
    definition = make_definition(
        [
            WorkflowNode(
                key="fan",
                label="fan",
                capability="fan",
                outputs=["out.json"],
                shard=WorkflowShardSpec(count=4, max_concurrency=2),
            )
        ]
    )
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, definition, executor)
    db_path = worker.leases.path
    try:
        ok = _poll_until(
            worker, lambda: _active_lease_count(db_path, job["id"], "fan") == 2, timeout=5
        )
        assert ok, "前两个分片未被 claim"
        time.sleep(0.2)
        worker._poll()
        assert _active_lease_count(db_path, job["id"], "fan") == 2, "max_concurrency hint 失效"
        shards = _node_shards(db_path, job["id"], "fan")
        statuses = [row["status"] for row in shards]
        assert statuses.count("running") == 2
        assert statuses.count("pending") == 2
        assert [json.loads(row["input_json"]) for row in shards] == [{"index": i} for i in range(4)]
        gate.set()
        ok = _poll_until(worker, lambda: _node_status(job_db, job["id"], "fan") == "completed")
        assert ok, "放行后 4 个分片未全部完成"
        assert len(_lease_rows(db_path, job["id"], "fan")) == 4
    finally:
        gate.set()
        worker.stop()


def test_failed_shard_fails_node_and_reduce_never_ready(tmp_path):
    """Item 13: 单分片 failed 且重试耗尽 → shard 节点 failed；reduce 不 ready。"""
    executor = FakeShardExecutor(fail_shards={1})
    definition = make_definition(
        [
            WorkflowNode(
                key="review",
                label="review",
                capability="review",
                outputs=["r.json"],
                shard=WorkflowShardSpec(count=2),
            ),
            WorkflowNode(
                key="aggregate",
                label="aggregate",
                capability="merge",
                after=["review"],
                outputs=["m.json"],
                reduce=WorkflowReduceSpec(from_node="review"),
            ),
        ]
    )
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, definition, executor)
    db_path = worker.leases.path
    try:
        ok = _poll_until(worker, lambda: _node_status(job_db, job["id"], "review") == "failed")
        assert ok, "shard 节点未因分片失败而 failed"
        time.sleep(0.2)
        worker._poll()
        assert _node_status(job_db, job["id"], "aggregate") == "pending", "reduce 不应 ready"
        assert all(c.node_key != "aggregate" for c in executor.contexts)
        statuses = sorted(row["status"] for row in _node_shards(db_path, job["id"], "review"))
        assert statuses == ["completed", "failed"]
        node = job_db.get_job_node(job["id"], "review")
        assert node is not None and node["error_message"] == "shard 1 failed"
        assert job_db.get_job(job["id"])["status"] == "failed"
    finally:
        worker.stop()


def test_every_shard_claim_goes_through_leases(tmp_path):
    """Item 14 (EXEC-SHARD-001 证据): lease 表行数 == 分片数。"""
    executor = FakeShardExecutor()
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor)
    db_path = worker.leases.path
    try:
        ok = _poll_until(worker, lambda: _node_status(job_db, job["id"], "review") == "completed")
        assert ok, "shard 节点未完成"
        leases = _lease_rows(db_path, job["id"], "review")
        assert len(leases) == 4, "每个分片必须经 leases.try_claim 取得独立 lease"
        assert len({row["execution_id"] for row in leases}) == 4
        assert all(row["status"] == "released" for row in leases)
        # 无绕过容量体系的 fan-out：shard 节点本身只有经 lease 的 node_runs
        runs = [run for run in job_db.list_node_runs(job["id"]) if run["node_key"] == "review"]
        assert len(runs) == 4
        assert all(run["status"] == "completed" for run in runs)
    finally:
        worker.stop()


def test_rerun_shard_node_rematerializes_shards(tmp_path):
    from server.app.db.transaction import write_transaction
    from server.app.jobs.atomic_mutations import mark_nodes_for_rerun

    executor = FakeShardExecutor()
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor)
    db_path = worker.leases.path
    try:
        assert _poll_until(worker, lambda: _node_status(job_db, job["id"], "review") == "completed")
        assert len(_node_shards(db_path, job["id"], "review")) == 4
        with write_transaction(db_path) as conn:
            mark_nodes_for_rerun(
                conn,
                str(job["id"]),
                ["review"],
                {"review": ["aggregate"]},
            )
        assert _node_shards(db_path, job["id"], "review") == []
        assert _poll_until(
            worker,
            lambda: len(_node_shards(db_path, job["id"], "review")) == 4,
        )
        assert _poll_until(worker, lambda: _node_status(job_db, job["id"], "review") == "completed")
    finally:
        worker.stop()


def test_apply_run_to_clears_shard_rows(tmp_path):
    from server.app.db.transaction import write_transaction
    from server.app.jobs.atomic_mutations import apply_run_to
    from server.app.workflows.sharding import materialize_shards

    executor = FakeShardExecutor()
    worker, _job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor)
    db_path = worker.leases.path
    try:
        with write_transaction(db_path) as conn:
            materialize_shards(
                conn,
                str(job["id"]),
                "review",
                [{"q": 0}],
                max_shards=4,
            )
        assert len(_node_shards(db_path, job["id"], "review")) == 1
        with write_transaction(db_path) as conn:
            apply_run_to(
                conn,
                str(job["id"]),
                "review",
                frozenset({"parse", "review"}),
            )
        assert _node_shards(db_path, job["id"], "review") == []
    finally:
        worker.stop()
