"""Per-task upload pipeline timing and the ``execution.reported`` event (#551).

The upload queue (``worker/upload/queue.py``) was the last black box in the
supply→consume chain: a backing-up queue could not say WHERE tasks sat —
waiting for a lane, in prepare (scan/compress/archive), in transfer, or in
the report round-trip. One timer instance rides each UploadTask; the queue
drops monotonic marks at stage boundaries and this module folds them into
one ``execution.reported`` line per task at finalize (delivered, rejected
or aborted alike — a stuck/failed delivery is exactly what the event exists
to make visible). Same never-raises discipline as ``worker.events``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from worker import events

# mark 顺序即跨度边界：submit→bulk_start = queue_wait，prepare 段、transfer 段、
# bulk_done→report_start = report 车道排队，report_start→finalize = report RTT
# （含重试退避）。缺失的 mark（中止路径）对应的跨度直接不出现在事件里。
_SPANS = (
    ("queue_wait", "submit", "bulk_start"),
    ("prepare", "bulk_start", "prepare_done"),
    ("transfer", "prepare_done", "bulk_done"),
    ("report_wait", "bulk_done", "report_start"),
)


@dataclass
class UploadReportTimer:
    """Monotonic marks for one upload task; created at queue submit."""

    marks: dict[str, float] = field(default_factory=dict)
    # 归档在 report 成功后随目录清理删除——_report 在清理前记下字节数。
    archive_bytes: int | None = None

    def __post_init__(self) -> None:
        self.marks["submit"] = time.monotonic()

    def mark(self, name: str) -> None:
        self.marks[name] = time.monotonic()


def mark(task: Any, name: str) -> None:
    """None-safe mark：计时器只由 queue.submit 创建；守卫对齐本模块的
    never-raise 纪律（车道函数里一次裸 AttributeError 会落进无人读的
    Future）。"""
    timer = getattr(task, "report_timer", None)
    if timer is not None:
        timer.mark(name)


def note_execution_reported(task: Any, outcome: str) -> None:
    """Emit execution.reported: per-stage spans + outcome + size.

    ``outcome``: delivered（204）/ rejected（Host 拒收，含租约 409）/
    aborted（关停或车道异常——marker 保留，下次启动重投）。
    report_seconds 是 report 车道的墙钟（含瞬时失败的重试退避——它直接
    决定租约 90s TTL 的生存压力）。
    """
    timer = task.report_timer
    marks = timer.marks if isinstance(timer, UploadReportTimer) else {}
    payload: dict[str, Any] = {
        "execution_id": task.execution_id,
        "node_key": task.node_key,
        "kind": task.exec_kind,
        "outcome": outcome,
        "archive_bytes": timer.archive_bytes if isinstance(timer, UploadReportTimer) else None,
    }
    for field_name, start, end in _SPANS:
        if start in marks and end in marks:
            payload[f"{field_name}_seconds"] = round(marks[end] - marks[start], 3)
    if "report_start" in marks:
        payload["report_seconds"] = round(time.monotonic() - marks["report_start"], 3)
    events.emit_event("execution.reported", payload)
