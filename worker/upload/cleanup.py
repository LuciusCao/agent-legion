"""Lease-aware filesystem cleanup for completed or moot upload tasks.

#1174 F3 起本模块同时持有恢复入口的 marker 甄别（``restore_task_from_marker``
——不可读丢弃 / 终态已交付跳过）与 unlink 失败臂的 tombstone 记录；终态
tombstone 的状态机与落点见 ``worker/upload/delivery_tombstone.py``。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from worker.execution.ownership import discard_owned_dir
from worker.upload.constants import PENDING_FILENAME
from worker.upload.delivery_tombstone import marker_delivery_finalized, record_undroppable_marker
from worker.upload.task import UploadTask


def drop_marker(task: UploadTask) -> bool:
    """Remove this lease's marker and owned directory after a final verdict.

    The UploadHandoff barrier prevents a new local attempt from touching this
    path until finalization signals ``delivery_done``. Marker lease validation
    remains a fail-closed defense for unexpected external writers. Missing or
    corrupt markers are removable orphans; the owner marker still decides
    whether the directory itself may be deleted.

    #1174 F3：marker unlink 失败（EACCES/EROFS 族——目录无写权，marker 也
    因此不可改写）不再上抛：调用点（report / lost 收尾）都已在 Host 终态
    之后，上抛只会把已交付结果当 aborted、滞留 marker 每重启重投（重投
    重放 Host 幂等 204/409——无数据风险、无限重复是噪声）。降级路径：state
    侧记 ``upload-delivered.json`` tombstone（restore 读到即跳过重投，状态
    机见 delivery_tombstone），目录清理仍走既有 rmtree(ignore_errors)——
    失败即滞留，与滞留归档同一人工清理语义。返回 False 覆盖「marker 仍在
    盘上」的全部形态：他 lease 的活 marker（新 attempt 所有）、tombstone
    化的 inert 残留、ownership 守卫否决的目录。
    """
    marker = task.execution_dir / PENDING_FILENAME
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    owned = str(payload.get("lease_id") or "")
    if owned and owned != str(task.lease_id):
        print(f"keeping pending marker for {task.execution_id}: owned by {owned!r}", flush=True)
        return False
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        # 窄捕获（OSError）：unlink 在不可写目录上的 EACCES/EROFS。降级语义
        # 见 docstring——终态已交付，收尾失败绝不重放 report 车道。
        print(
            f"pending marker undroppable for {task.execution_id}: {exc}; recording tombstone",
            flush=True,
        )
        record_undroppable_marker(task, exc)
    if not discard_owned_dir(task.execution_dir, task.lease_id):
        return False
    shutil.rmtree(task.execution_dir, ignore_errors=True)
    return True


def restore_task_from_marker(child: Path, work_root: Path) -> UploadTask | None:
    """恢复入口的 marker 甄别：从 marker 重建任务；None = 不可重投。

    两个 None 臂（#1174 F3 收口进本模块：marker 生命周期归 cleanup）：

    - marker 不可读（截断 / 字段畸形 / IO 失败）：rmtree 丢弃该目录——
      marker 经 atomic_write 落盘，读不出即真损坏而非半截写（语义原样
      迁移自 queue.restore 的逐目录遏制臂）。
    - 终态已交付（tombstone 命中同 execution_id + lease_id）：跳过重投、
      best-effort 清目录——重投只会重放 Host 幂等应答（204 重复提交 /
      409 租约拒绝），每重启一轮的重复是恢复噪声；目录不可写时滞留，
      与滞留归档同一人工清理语义。
    """
    marker = child / PENDING_FILENAME
    try:
        task = UploadTask.from_json(json.loads(marker.read_text(encoding="utf-8")), work_root)
    except Exception as exc:
        # #204 broad-except audit: 逐目录遏制。marker 的逃逸族混族
        # ——解码 ValueError、from_json 的 KeyError/TypeError（字段
        # 畸形）、read_text 的 OSError——统一语义是"marker 已损坏"。
        # 吞是对的：一个坏 marker 不得阻断其余待恢复结果重新入队；
        # marker 经 atomic_write 落盘（tmp+fsync+replace），读不出
        # 即真损坏而非半截写，rmtree 丢弃该目录是设计选择。日志
        # 保全：print 记录 marker 路径与异常。
        print(f"discarding unreadable upload marker {marker}: {exc}", flush=True)
        shutil.rmtree(child, ignore_errors=True)
        return None
    if marker_delivery_finalized(task):
        print(f"skipping re-queue of delivered upload marker {marker}", flush=True)
        shutil.rmtree(child, ignore_errors=True)
        return None
    return task
