"""Hydration defer 公告板：把升级后的悬挂清单行 defer 下发给 job 详情接口（#887）。

workflow worker 的悬挂行连续计数（``workflow_worker/hydration_dangling``）
达到升级阈值、且没有在途生产者会重写该名字时，job 的评估维持 defer——
UI 上节点仍是「等待中」，用户无法区分普通排队与「输入恢复不全卡住」。

本模块是该状态的唯一下发面：workflow worker（Host 进程内线程，单副本形态，
见 docs/architecture/deployment.md「单副本约束」）在升级时 ``publish``，
job 详情查询经 ``for_job`` 读取并挂到等待中节点上。刻意不落库：

- 公告的生命周期与悬挂计数完全一致——计数只活在 worker 进程内存里，
  重启后重新计数、最坏 N 轮后重新上板；落库反而会在重启后留下无人清理
  的陈旧行（例如重启期间用户已按提示重跑生产节点）；
- 只读诊断，不是执行态：不进 EXEC-GENERATION-002 写面，不与状态机竞争。

只收「维持 defer」的升级项：被释放（在途生产者会重写）的名字不挡评估，
无需提示。写端只有 worker 的 poll 线程，读端是 API 线程，用锁保护整份
映射的替换。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass

from server.app.workflows.workflow_branching import RUNNABLE_STATUSES


@dataclass(frozen=True)
class HydrationDeferNotice:
    """一个维持 defer 的悬挂输入：原因、建议重跑的生产节点、受阻的等待节点。"""

    input_name: str
    outcome: str
    rerun_nodes: tuple[str, ...]
    waiting_nodes: tuple[str, ...]


class HydrationDeferBoard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_job: dict[str, tuple[HydrationDeferNotice, ...]] = {}

    def publish(self, job_id: str, notices: Iterable[HydrationDeferNotice]) -> None:
        """整体替换 job 的公告；空集即撤下。"""
        snapshot = tuple(notices)
        with self._lock:
            if snapshot:
                self._by_job[job_id] = snapshot
            else:
                self._by_job.pop(job_id, None)

    def for_job(self, job_id: str) -> tuple[HydrationDeferNotice, ...]:
        with self._lock:
            return self._by_job.get(job_id, ())

    def by_waiting_node(self, job_id: str) -> dict[str, list[HydrationDeferNotice]]:
        """等待节点 → 挡住它的公告（job 详情投影用）。"""
        grouped: dict[str, list[HydrationDeferNotice]] = {}
        for notice in self.for_job(job_id):
            for node_key in notice.waiting_nodes:
                grouped.setdefault(node_key, []).append(notice)
        return grouped


#: 进程级单例：worker 账本默认写入这里，job 详情查询从这里读。
HYDRATION_DEFER_BOARD = HydrationDeferBoard()


def node_defer_view(
    notices: list[HydrationDeferNotice] | None, node_status: str
) -> dict[str, list[str]] | None:
    """job 详情的节点投影：只给仍在等待（可运行态）的节点，其余为 None。

    公告按 worker 上一轮的状态算出，节点此后已被派发/终态时不再显示。
    """
    if not notices or node_status not in RUNNABLE_STATUSES:
        return None
    return {
        "inputs": sorted({n.input_name for n in notices}),
        "reasons": sorted({n.outcome for n in notices}),
        "rerun_nodes": sorted({key for n in notices for key in n.rerun_nodes}),
    }
