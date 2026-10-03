"""Ready-gate hydration 的悬挂清单行兜底（#827）。

hydration 的纪律是「恢复不全 → 不缓存评估、下一轮重试」（对象缺失可能是
瞬时的，不能把本地缺失误判为真缺失）。但清单行**悬挂**时——对象已被删
（``object_missing``）或字节与行不符（``hash_mismatch``）——重试永远不会
自愈，这条纪律退化为无声的永久 defer：整个 job 的评估被挡住，连会重写该
名字的上游生产者都派发不出去（#827 现场：全量重跑后 candidates 恒为 0）。

本模块按 (job, 名字) 记录**同一清单行**连续悬挂的轮数。达到
``DANGLING_ESCALATION_PASSES`` 后：

- **可释放**：该名字不是任何边的条件产物，且每个声明它为 input 的可运行
  节点都被一个未终态的**其他**生产者挡住（``_has_unfinished_implicit_producer``
  同口径）——这些消费者本轮本来就不会就绪，名字会被在途生产者重写。
  它退出 defer 集，按「无清单行 = 真缺失」口径参与评估（job 照常评估、
  生产者照常派发、可缓存）；
- **不可释放**（没有在途生产者会重写它、或它是条件产物——缺失会被当成
  条件为假而把分支静默标 not_applicable）：继续 defer（不猜），但升级为
  带 suggested action 的 WARNING——不再无声。

升级日志每个 (job, 名字, 行身份) 只打一次。行身份变化（新写者登记、
行被退役后重登）即重新计数；名字恢复成功或 job 离开可运行集即清除。
状态只活在 workflow worker 进程内存里：重启后重新计数，最坏多 defer
N 轮，不影响正确性。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.app.executors.artifact_restore import (
    FAILED,
    HASH_MISMATCH,
    RESTORED,
    restore_outcome_from_manifest_row,
)
from server.app.workflows.condition_barrier import TERMINAL_SUCCESS_STATUSES
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES, effective_node_statuses
from server.app.workflows.workflow_consumption import artifact_producers

if TYPE_CHECKING:
    from pathlib import Path

    from server.app.services.job_artifact_objects import JobArtifactObjectStore

logger = logging.getLogger(__name__)

#: 清单行悬挂（重试不会自愈）的两种恢复结果；其余失败按瞬时处理。
OBJECT_MISSING = "object_missing"
DANGLING_OUTCOMES = frozenset({OBJECT_MISSING, HASH_MISMATCH})

#: 同一清单行连续悬挂多少轮后升级（轮间隔 0.2–3s，量级为十秒）。瞬时形态
#: （promote 的 copy 已落、行未登记的毫秒窗口）远小于此。
DANGLING_ESCALATION_PASSES = 5


@dataclass
class _Streak:
    identity: tuple[str, ...]
    count: int = 1
    reported: bool = False


def _row_identity(outcome: str, row: dict[str, Any]) -> tuple[str, ...]:
    return (
        outcome,
        str(row.get("node_key") or ""),
        str(row.get("storage_key") or ""),
        str(row.get("content_hash") or ""),
        str(row.get("uploaded_at") or ""),
    )


def rewrite_pending(definition: WorkflowDefinition, statuses: dict[str, str], name: str) -> bool:
    """名字的全部可运行 input 消费者都被未终态的其他生产者挡住（且非条件产物）。"""
    if any(e.condition is not None and e.condition.artifact == name for e in definition.edges):
        return False
    effective = effective_node_statuses(definition, statuses)
    producers = artifact_producers(definition).get(name, set())
    consumers = [
        key
        for key, node in definition.nodes.items()
        if name in node.inputs and effective.get(key, "pending") in RUNNABLE_STATUSES
    ]
    return bool(consumers) and all(
        any(
            producer != consumer
            and effective.get(producer, "pending") not in TERMINAL_SUCCESS_STATUSES
            for producer in producers
        )
        for consumer in consumers
    )


def restore_rows(
    store: JobArtifactObjectStore,
    job_id: str,
    job_dir: Path,
    missing: list[str],
    rows_by_name: dict[str, dict[str, Any]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """逐名按清单行恢复，返回未恢复的名字 → (outcome, 清单行)。

    ``FAILED``（流读取失败等）再经 HEAD 探测分类：对象不存在即悬挂行
    （``object_missing``），探测本身失败或对象存在则维持瞬时类 ``failed``。
    """
    failures: dict[str, tuple[str, dict[str, Any]]] = {}
    for name in missing:
        row = rows_by_name.get(name)
        if row is None:
            continue
        outcome = restore_outcome_from_manifest_row(
            store, job_id=job_id, job_dir=job_dir, name=name, row=row
        )
        if outcome == FAILED and _object_absent(store, row):
            outcome = OBJECT_MISSING
        if outcome != RESTORED:
            failures[name] = (outcome, row)
    return failures


def _object_absent(store: JobArtifactObjectStore, row: dict[str, Any]) -> bool:
    if store.storage is None:
        return False
    try:
        return store.storage.head_object(str(row["storage_key"])) is None
    except Exception:
        # #204 broad-except audit: classification probe only. An unreadable
        # HEAD (botocore/network surface, no business family) cannot prove
        # the object is gone, so the failure stays transient-class: the
        # caller keeps the uncached retry discipline instead of counting a
        # dangling streak. The restore failure itself was already logged
        # with its traceback by restore_outcome_from_manifest_row.
        return False


def settle_unrestored(
    dangling: DanglingManifestStreaks | None,
    job_id: str,
    failures: dict[str, tuple[str, dict[str, Any]]],
    definition: WorkflowDefinition,
    statuses: dict[str, str],
) -> frozenset[str]:
    """本轮未恢复集减去兜底释放的名字（无账本时原样返回）。"""
    if dangling is None:
        return frozenset(failures)
    return frozenset(failures) - dangling.observe(job_id, failures, definition, statuses)


class DanglingManifestStreaks:
    """per-worker 的悬挂清单行连续轮次账本（只由 poll 线程访问）。"""

    def __init__(self, threshold: int = DANGLING_ESCALATION_PASSES) -> None:
        self.threshold = threshold
        self._streaks: dict[str, dict[str, _Streak]] = {}

    def observe(
        self,
        job_id: str,
        failures: dict[str, tuple[str, dict[str, Any]]],
        definition: WorkflowDefinition,
        statuses: dict[str, str],
    ) -> frozenset[str]:
        """记录本轮恢复失败（名字 → (outcome, 清单行)），返回可释放的名字。

        只有 ``DANGLING_OUTCOMES`` 计数；瞬时类失败与本轮恢复成功的名字
        清除旧计数（连续性被打断）。
        """
        previous = self._streaks.get(job_id, {})
        current: dict[str, _Streak] = {}
        for name, (outcome, row) in failures.items():
            if outcome not in DANGLING_OUTCOMES:
                continue
            identity = _row_identity(outcome, row)
            streak = previous.get(name)
            if streak is not None and streak.identity == identity:
                streak.count += 1
                current[name] = streak
            else:
                current[name] = _Streak(identity)
        if current:
            self._streaks[job_id] = current
        else:
            self._streaks.pop(job_id, None)
        releasable: set[str] = set()
        for name, streak in sorted(current.items()):
            if streak.count < self.threshold:
                continue
            release = rewrite_pending(definition, statuses, name)
            if release:
                releasable.add(name)
            if not streak.reported:
                streak.reported = True
                self._report(job_id, name, streak, release, definition)
        return frozenset(releasable)

    def describe(self, job_id: str) -> dict[str, str]:
        """defer 日志用：名字 → ``<outcome> <count>/<threshold>``。"""
        return {
            name: f"{streak.identity[0]} {streak.count}/{self.threshold}"
            for name, streak in sorted(self._streaks.get(job_id, {}).items())
        }

    def retain(self, job_ids: set[str]) -> None:
        """丢弃已离开可运行集的 job（完成/失败/暂停/删除）。"""
        for job_id in [job_id for job_id in self._streaks if job_id not in job_ids]:
            del self._streaks[job_id]

    def _report(
        self,
        job_id: str,
        name: str,
        streak: _Streak,
        released: bool,
        definition: WorkflowDefinition,
    ) -> None:
        outcome, node_key, storage_key = streak.identity[:3]
        producers = sorted(artifact_producers(definition).get(name, set())) or [node_key]
        if released:
            action = (
                "treating it as absent so the job keeps scheduling; "
                f"in-flight producer(s) {producers} will rewrite it"
            )
        else:
            action = (
                "evaluation stays deferred; suggested action: rerun producer node(s) "
                f"{producers} (or the job's entry node) to regenerate it"
            )
        logger.warning(
            "job %s hydration: manifest row for input %s is dangling (%s; node=%s key=%s) "
            "for %d consecutive passes; %s",
            job_id,
            name,
            outcome,
            node_key,
            storage_key,
            streak.count,
            action,
        )
