"""Upload task data shape (split from ``queue.py`` for the file budget, #551).

The queue module owns the lanes and delivery; this module owns the task
record: identity, persisted marker (de)serialization, the direct-upload
verdict, and the runtime-only delivery state (prepared artifacts, heartbeat
handles, the #551 stage timer).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PENDING_VERSION = 1


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
    heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    heartbeat_thread: threading.Thread | None = None
    # #352: 本任务租约归属的批量心跳 registry（_deliver_bulk 接管/恢复时
    # 设置）。None = 旧单条心跳模式（无 registry 的单测路径）。
    heartbeat_registry: Any = None
    # bulk 车道产物，交给 report 车道；运行时状态，不持久化——崩溃恢复的任务
    # 一律从 bulk 车道重进，prepare 与 artifact 上传会原样重做。
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
        )
