"""Upload task data shape (split from ``queue.py`` for the file budget, #551).

The queue module owns the lanes and delivery; this module owns the task
record: identity, persisted marker (de)serialization, the direct-upload
verdict, the archive-ceiling verdicts (precheck vs. degrade-recycle and
their snapshot-trust split, #1174/#1184), and the runtime-only delivery
state (prepared artifacts, heartbeat handles, the #551 stage timer).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.code_contract import MIN_RESULT_ARCHIVE_BYTES

_PENDING_VERSION = 1


def degrade_ceiling(task: UploadTask, snapshot_stale: bool = False) -> int:
    """降级回收位的归档上限口径（#1174/#1184 矩阵，三个回收位同源）：

    - ``snapshot_stale=True``（已收到 413——唯一带 Host 大小判决的回收
      位）：无条件协议下限。413 本身就是「claim 快照过期」的判决信号：
      Host 可能重启后下调了 ``agent_workers.max_archive_bytes``（或
      marker 持久值本就来自旧配置），收到判决后本地任何上限知识都
      不可信；按协议保证的最小上限裁，重报归档对任何合法 Host 配置
      必可提交。
    - 恢复任务的持久值（``max_archive_bytes_restored``，#1184 复审）：
      同样无条件下限——快照双向可过期，非 413 回收位用它裁会撞
      「裁进已失效口径 → 重报 413 → 闸已回收拒绝二次 → 终态删 marker
      丢结果」的角落链；下限的代价只是归因观测面被多裁。
    - 其余（在线值、无 Host 大小信号——换写失败回落、finalize 拒写
      臂）：正值按 claim 下发的实际值；0（旧 Host / #1174 前旧
      marker）按协议下限——快照仍是本地最好知识。
    语义矩阵见 report_policy 模块 docstring。"""
    if snapshot_stale or task.max_archive_bytes_restored:
        return MIN_RESULT_ARCHIVE_BYTES
    return task.max_archive_bytes or MIN_RESULT_ARCHIVE_BYTES


def precheck_ceiling(task: UploadTask) -> int:
    """预检位的归档上限口径（#1184 复审矩阵）：只信在线 claim 值（Host
    刚随 claim 下发，可信窗口内）；恢复任务读出的持久值不参与预检
    （0 = 不猜）——快照双向可过期，上调后本地预检会把 Host 现在完全
    能收的归档误杀为 failed（成功执行被永久判败），只能照发交 Host
    的 413 判决 + 报告循环回收臂兜底。"""
    return 0 if task.max_archive_bytes_restored else task.max_archive_bytes


class PendingUploadExists(RuntimeError):
    """#203：execution dir 已带未投递 marker——该目录归 UploadQueue 所有。"""


@dataclass
class UploadTask:
    """Everything needed to deliver one execution's result to the Host."""

    execution_id: str
    lease_id: str
    execution_dir: Path
    node_key: str
    status_fields: dict[str, str]
    # "process": run post-processing (scan/compress/archive) then report.
    # "prebuilt": metadata is final (pre-process failure / pre-start cancel).
    kind: str
    # "agent"（缺省）或 "code"（批次 2）：上面的 kind 已被
    # "process"/"prebuilt" 占用，agent/code 维度用 exec_kind 表达（勿复用）。
    exec_kind: str = "agent"
    # code 执行的结果（status/error_message/auth_failure_connection），由
    # code_runner 在进程退出后填入；随 pending marker 持久化供崩溃恢复。
    code_result: dict[str, Any] | None = None
    exit_code: int = 1
    expected_outputs: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    prebuilt_metadata: dict[str, Any] | None = None
    # #160 D12: claim manifest 的 artifact_uploads（name → {storage_key, url}
    # presigned PUT）。非空时产物直传 S3、result.tar.gz 不再内嵌产物；
    # 空 = 旧通道（CAS POST + tar 内嵌）。不持久化：presigned URL 会过期，
    # 崩溃恢复的任务从 bulk 车道重进时走旧通道（Host 两种形态都收）。
    artifact_uploads: dict[str, Any] = field(default_factory=dict)
    # #755 codex P1：Host 经 claim 下发的 agent_workers.max_archive_bytes
    # 实际值（在线任务预检/换轨的判定口径）；0 = 未知（旧 Host 未下发 /
    # #1174 前的旧 marker 无字段）——预检不猜上限、交 Host 413 判决，
    # 413 回收臂则按协议下限裁剪（语义矩阵见 report_policy 模块
    # docstring）。#1174 F1 起随 marker 持久化（与 artifact_uploads 的
    # 不持久化纪律不同：presigned URL 会过期而纯 int 不会）；#1184
    # 复审起持久值只作诊断/观测锚点——快照双向可过期（Host 重启下调
    # → 413 兜底；上调 → 预检误杀成功执行），预检与回收裁剪只信在线
    # 值，恢复任务一律不预检（见 precheck_ceiling / degrade_ceiling）。
    max_archive_bytes: int = 0
    # #1184 复审：max_archive_bytes 的来源标记——True = 从 pending
    # marker 读回（恢复任务，快照可双向过期）；False = 在线 claim 注入
    # （execution/run 与 code_runner 的构造路径，可信）。运行态、不持久
    # 化（from_json 读回的值按定义就是恢复值，无需往返）。
    max_archive_bytes_restored: bool = False
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    heartbeat_thread: threading.Thread | None = None
    # #352: 本任务租约归属的批量心跳 registry（_deliver_bulk 接管/恢复时
    # 设置）。None = 旧单条心跳模式（无 registry 的单测路径）。
    heartbeat_registry: Any = None
    # #644：租约判死共享事件。批量 registry 的 entry（start_upload_heartbeat
    # 注册）与 legacy 单拍线程（HeartbeatConfig）都指向同一个对象——beat 面
    # 的 409/lost verdict（batch lost 列表、单拍 401/409）一经触发，_report
    # 的重试循环下一轮即终态放弃：执行已不归本 worker，结果是 moot 的。
    # 运行态不持久化：重启恢复的任务从「未判死」起步，由恢复后的首拍或首次
    # report 重新判定（409 report 本身仍是终态出口）。
    ownership_lost: threading.Event = field(default_factory=threading.Event)
    # UploadQueue 的本机 handoff barrier：同 execution_id 的新 attempt 在
    # 重用 execution_dir 前先判死旧上传并等待此事件。它只在旧任务完成全部
    # 文件收尾、registry/status/depth 记账后置位，因此目录不会被两代 attempt
    # 同时读写（#644 收口）。运行态，不持久化。
    delivery_done: threading.Event = field(default_factory=threading.Event)
    # _finalize 的幂等闩；只在 UploadHandoff 的锁下读写。正常结构只有车道
    # 外层一个 finalize owner，这个字段是防御线，避免未来异常分支再次把
    # depth 减成负数或重复发 execution.reported。
    finalize_started: bool = False
    # bulk 车道产物，交给 report 车道；运行时状态，不持久化——崩溃恢复的任务
    # 一律从 bulk 车道重进，prepare 与 artifact 上传会原样重做。
    # #843 v2 起 prepared_metadata 是写状态（metadata 随归档 result.json 交
    # 付，report 车道不再读它）；保留字段仅为降级闸（ReportDegradeGate）挂
    # 判败载荷的调试/观测锚点。
    prepared_metadata: dict[str, Any] | None = None
    prepared_archive: Path | None = None
    # #551 观测：分段计时器（submit 时创建；运行态，不入 marker——崩溃恢复
    # 的任务重新计时，queue_wait 从重新入队起算）。
    report_timer: Any = None

    def is_direct_upload(self, outputs: list[str]) -> bool:
        """#160 D12 直传判定（#201 单点收敛）：manifest 带 artifact_uploads 且
        每个产出都有上传规格才走直传 S3 通道；否则整体回落旧通道（CAS POST +
        tar 内嵌）。归档是否内嵌产物（upload_prepare / code_runner 的
        prepare_result）与上传通道（_bulk_transfer）必须用同一判定，否则产物
        既不在 tar 里也没直传。注意 DirectUploadError 的回落路径会把
        artifact_uploads 清空再重取判定，本方法天然随之变 False。"""
        return bool(self.artifact_uploads) and all(
            name in self.artifact_uploads for name in outputs
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": _PENDING_VERSION,
            "execution_id": self.execution_id,
            "lease_id": self.lease_id,
            "node_key": self.node_key,
            "status_fields": self.status_fields,
            "kind": self.kind,
            "exec_kind": self.exec_kind,
            "code_result": self.code_result,
            "exit_code": self.exit_code,
            "expected_outputs": list(self.expected_outputs),
            "command": list(self.command),
            "prebuilt_metadata": self.prebuilt_metadata,
            # #1174 F1：随 marker 持久化（纯 int、无过期问题），恢复任务的
            # 预检/回收裁剪与在线任务同口径；旧版本 marker 缺字段归 0。
            "max_archive_bytes": int(self.max_archive_bytes),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any], work_root: Path) -> UploadTask:
        if int(payload.get("version", 0)) != _PENDING_VERSION:
            raise ValueError(f"unsupported upload marker version: {payload.get('version')!r}")
        execution_id = str(payload["execution_id"])
        return cls(
            execution_id=execution_id,
            lease_id=str(payload["lease_id"]),
            execution_dir=work_root / execution_id,
            node_key=str(payload["node_key"]),
            status_fields={str(k): str(v) for k, v in dict(payload["status_fields"]).items()},
            kind=str(payload["kind"]),
            exec_kind=str(payload.get("exec_kind") or "agent"),
            code_result=payload.get("code_result"),
            exit_code=int(payload.get("exit_code", 1)),
            expected_outputs=tuple(str(name) for name in payload.get("expected_outputs", [])),
            command=tuple(str(part) for part in payload.get("command", [])),
            prebuilt_metadata=payload.get("prebuilt_metadata"),
            max_archive_bytes=int(payload.get("max_archive_bytes") or 0),
            # #1184 复审：marker 读回的上限是可过期快照（来源标记，运行态
            # 不持久化）——预检不猜、回收按下限（precheck_ceiling /
            # degrade_ceiling 读此标记分流）。
            max_archive_bytes_restored=True,
        )
