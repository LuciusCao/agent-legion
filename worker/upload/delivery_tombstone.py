"""终态收尾已完成的 marker 收口（#1174 F3：codex 二轮 P2）。

场景：execution_dir 不可写（EACCES/EROFS 族）时 ``cleanup.drop_marker`` 的
``marker.unlink()`` 物理上不可能成功——本机无法用「删 marker」表达「终态
收尾已完成」，重启 restore 每次都重投，重投只会重放 Host 的幂等终态应答
（204 重复提交 / 409 租约拒绝）或同一判决，无数据风险，但每重启一轮的
无限重复是恢复噪声。

状态机（marker 可删性 × Host 终态应答；恢复入口 ``queue.restore`` 的跳过
语义由本模块的 tombstone 驱动）：

- marker 可删（常规）：204 / 409 / 判决终态后 marker 即删，restore 不再
  见，零噪声。
- marker 不可删（EACCES/EROFS 族，目录无写权——marker 也因此不可改写，
  「把终态写进 marker」不可行）：终态收尾已完成 → 在 state 侧 incident
  目录写 ``upload-delivered.json`` tombstone（键 execution_id + lease_id
  ——旧 lease 的 tombstone 不得压制 Host 重排队后新 attempt 重写的
  marker；载荷另记 ``outcome``：``delivered`` = Host 已收下（204）、
  ``rejected`` = 判决终态未收下（降级重报仍被拒 / 判决丢弃）、
  ``lost`` = 租约死——三者的共同点都是重投无意义，restore 读到即跳过
  重投、只做 best-effort 目录清理）。
  - 204（已提交）：重投本会吃 Host 幂等 204（提交侧按 lease 幂等），跳过
    同样正确且省一轮重放。
  - 409（租约终态）：重投吃确定性 409——无数据风险，重复才是噪声，
    跳过即归零。
  - tombstone 写失败（state 目录同样不可写 / evidence root 未配置）：
    fail-open 到既有形态（每重启一次有界重投、吃一次幂等应答），不可能
    更差。

落点复用 ``state_evidence.incident_dir``：上传链路既有的唯一 state 侧
可写位置（``write_degraded_empty_archive`` 的滞留归档同住此处——report
车道只按路径读归档字节，不关心归档住在哪），零新增接线；incident 目录
按 ``<execution_id>__<node_key>`` pair-scoped，同 pair 重入时新记录覆盖
旧记录（与取证 dump 的 fresh-wins 语义一致）。无 TTL，与滞留归档同一
人工清理语义。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from worker import state_evidence
from worker._atomic import atomic_write

if TYPE_CHECKING:
    from worker.upload.task import UploadTask

# incident 目录内的终态 tombstone 文件名（与滞留归档 result.tar.gz 同住）。
DELIVERED_TOMBSTONE_FILENAME = "upload-delivered.json"


def record_undroppable_marker(task: UploadTask, exc: OSError, outcome: str) -> None:
    """``drop_marker`` 的 unlink 失败臂：state 侧记「终态收尾已完成」tombstone。

    ``outcome`` 是调用点的收尾形态（``delivered`` = Host 已收下 /
    ``rejected`` = 判决终态未收下 / ``lost`` = 租约死）——三者重投都只会
    重放幂等应答或同一判决，tombstone 据此统一跳过；字段也让人从滞留
    记录上区分「已交付」与「判决后丢弃」。

    best-effort：evidence root 未配置（单测路径）或 state 目录同样不可写
    时仅记日志——fail-open 到「每重启一次有界重投」的既有形态，绝不让
    记录失败炸回 report 车道（那正是本模块要消除的路径）。"""
    incident = state_evidence.incident_dir(task.execution_id, task.node_key)
    if incident is None:
        print(
            f"cannot record delivery tombstone for {task.execution_id}: evidence root"
            " unconfigured; the stranded marker re-reports once per restart",
            flush=True,
        )
        return
    record = {
        "version": 1,
        "execution_id": task.execution_id,
        "lease_id": task.lease_id,
        "node_key": task.node_key,
        "outcome": outcome,
        "unlink_error": str(exc),
        "recorded_at": time.time(),
    }
    try:
        incident.mkdir(parents=True, exist_ok=True)
        atomic_write(
            incident / DELIVERED_TOMBSTONE_FILENAME,
            json.dumps(record, ensure_ascii=False),
        )
    except OSError as write_exc:
        # 窄捕获（OSError：mkdir/mkstemp/replace 的失败族）。吞是对的：
        # tombstone 是恢复噪声的收口件而非交付链路的必要条件，写不进时
        # 语义退回「每重启一次有界重投」（既有形态、无数据风险）；上抛则
        # 会让 drop_marker 把已交付的结果当 aborted 处置。日志保全：print
        # 记录 execution_id 与两段异常。
        print(
            f"delivery tombstone write failed for {task.execution_id}: {write_exc};"
            " the stranded marker re-reports once per restart",
            flush=True,
        )


def marker_delivery_finalized(task: UploadTask) -> bool:
    """restore 的 tombstone 查询：True = 该 (execution_id, lease_id) 的终态
    收尾已完成（delivered / rejected / lost——outcome 不影响跳过判定）且
    marker 本机删不掉——重投只会重放幂等应答或同一判决。键匹配含
    execution_id（incident 目录名经字符清洗，两个原始 id 可能折叠成同一
    段名，载荷字段是精确语义）。"""
    incident = state_evidence.incident_dir(task.execution_id, task.node_key)
    if incident is None:
        return False
    try:
        payload = json.loads((incident / DELIVERED_TOMBSTONE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        str(payload.get("execution_id") or "") == task.execution_id
        and str(payload.get("lease_id") or "") == task.lease_id
    )
