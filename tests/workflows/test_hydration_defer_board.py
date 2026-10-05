"""Hydration defer 公告板（#887）：悬挂行升级后的下发面与生命周期。

账本（``DanglingManifestStreaks``）只把「维持 defer」的升级项上板：未到阈值、
被释放（在途生产者会重写）的名字不上板；计数清除（恢复成功 / 行身份变化 /
job 离开可运行集）即撤下。job 详情只给仍在等待的节点投影。
"""

from __future__ import annotations

import pytest

from server.app.services.hydration_defer_board import (
    HydrationDeferBoard,
    HydrationDeferNotice,
    node_defer_view,
)
from server.app.workflow_worker.hydration_dangling import (
    DANGLING_ESCALATION_PASSES,
    DanglingManifestStreaks,
)
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)

pytestmark = pytest.mark.no_db

_ROW = {"node_key": "a", "storage_key": "jobs/w/j/a_out.json", "content_hash": "h"}


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="cap_a", outputs=["a_out.json"]),
            "b": WorkflowNode(
                key="b", label="B", capability="cap_b", after=["a"], inputs=["a_out.json"]
            ),
            "c": WorkflowNode(key="c", label="C", capability="cap_c", after=["a"]),
        },
    )


_STUCK = {"a": "completed", "b": "pending", "c": "pending"}
_FAILURE = {"a_out.json": ("hash_mismatch", _ROW)}


def _observe(ledger: DanglingManifestStreaks, passes: int, statuses=_STUCK) -> None:
    for _ in range(passes):
        ledger.observe("j1", _FAILURE, _definition(), statuses)


def test_stuck_name_is_published_only_at_threshold() -> None:
    board = HydrationDeferBoard()
    ledger = DanglingManifestStreaks(board=board)

    _observe(ledger, DANGLING_ESCALATION_PASSES - 1)
    assert board.for_job("j1") == ()

    _observe(ledger, 1)
    assert board.for_job("j1") == (
        HydrationDeferNotice(
            input_name="a_out.json",
            outcome="hash_mismatch",
            rerun_nodes=("a",),
            # job 级 gate：与 a_out.json 无关的独立分支 c 同样被挡（codex #1018 P2）。
            waiting_nodes=("b", "c"),
        ),
    )


def test_notice_withdrawn_when_name_restores_or_job_leaves_runnable_set() -> None:
    board = HydrationDeferBoard()
    ledger = DanglingManifestStreaks(board=board)
    _observe(ledger, DANGLING_ESCALATION_PASSES)
    assert board.for_job("j1")

    ledger.observe("j1", {}, _definition(), _STUCK)
    assert board.for_job("j1") == ()

    _observe(ledger, DANGLING_ESCALATION_PASSES)
    assert board.for_job("j1")
    ledger.retain(set())
    assert board.for_job("j1") == ()


def test_released_name_is_not_published() -> None:
    """在途生产者会重写（a 未终态）：名字被释放、job 照常评估，不需要提示。"""
    board = HydrationDeferBoard()
    ledger = DanglingManifestStreaks(board=board)
    released = frozenset()
    for _ in range(DANGLING_ESCALATION_PASSES):
        released = ledger.observe(
            "j1", _FAILURE, _definition(), {"a": "pending", "b": "stale", "c": "pending"}
        )
    assert released == {"a_out.json"}
    assert board.for_job("j1") == ()


def test_node_view_only_for_waiting_nodes() -> None:
    notice = HydrationDeferNotice(
        input_name="a_out.json",
        outcome="object_missing",
        rerun_nodes=("a",),
        waiting_nodes=("b",),
    )
    assert node_defer_view([notice], "pending") == {
        "inputs": ["a_out.json"],
        "reasons": ["object_missing"],
        "rerun_nodes": ["a"],
    }
    assert node_defer_view([notice], "stale") is not None
    assert node_defer_view([notice], "running") is None
    assert node_defer_view(None, "pending") is None
