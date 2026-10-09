"""Worker 上传队列测试的共享桩/工具（自 test_worker_upload_queue.py 拆出）。

供 tests/workers 下上传队列一族测试文件共用的 fake client、执行目录种子、
任务构造与队列装配；命名保持下划线前缀（沿用被拆文件的用例现场，零改动迁移）。
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import threading
from pathlib import Path
from typing import Any

from shared.code_contract import RESULT_METADATA_MEMBER
from worker.execution.heartbeat_batch import BatchHeartbeatRegistry
from worker.status import ExecutionStatusReporter
from worker.upload import queue as upload_queue  # noqa: F401  (调用方 monkeypatch 用)
from worker.upload.queue import UploadQueue, UploadTask


def read_result_metadata(archive: Path) -> dict[str, Any]:
    """v2（#843 PR-2）：上报元数据在归档 ``result.json`` 成员里——fake 从归档
    读回，钉住「写入链必须落成员」的契约（缺成员即断言失败，测试直接暴露）。"""
    with tarfile.open(archive) as tar:
        member = tar.extractfile(RESULT_METADATA_MEMBER)
        assert member is not None, "result archive is missing the result.json member"
        return json.loads(member.read())


class QueueFakeClient:
    def __init__(self, report_status: int = 204) -> None:
        self.reports: list[dict] = []
        self.uploads: dict[str, bytes] = {}
        self.heartbeats = 0
        self.report_status = report_status
        self.report_errors = 0
        self.heartbeats_at_report: list[int] = []
        # report 时刻 execution dir 还在（成功后即被清）：记录留痕文件此刻的存在。
        self.stderr_trace_seen: list[bool] = []

    def upload_artifact(self, path: Path) -> str:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.uploads[digest] = data
        return f"sha256:{digest}"

    def report(self, execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        self.heartbeats_at_report.append(self.heartbeats)
        # #1147 评审 P3-1 关联：chmod 自锁的 run 目录下 is_file 对 EACCES
        # 会抛（pathlib 只豁免 ENOENT 族）——探针是纯观测面，OSError 一律
        # 按 False 记，不得让 fake 自身炸掉 report 车道。
        try:
            stderr_seen = (
                archive.parent / "job" / "runs" / "node_a" / "worker" / "agent-stderr.log"
            ).is_file()
        except OSError:
            stderr_seen = False
        self.stderr_trace_seen.append(stderr_seen)
        if self.report_errors > 0:
            self.report_errors -= 1
            raise RuntimeError("download failed: /x: timed out")
        self.reports.append(read_result_metadata(archive))
        return self.report_status, b""

    def heartbeat(self, execution_id: str, lease_id: str) -> tuple[int, list[str]]:
        self.heartbeats += 1
        return 204, []

    def heartbeat_batch(self, leases: list[tuple[str, str]]) -> tuple[int, dict]:
        return 200, {
            "renewed": [execution_id for execution_id, _ in leases],
            "lost": [],
            "cancelled_execution_ids": [],
        }


def _execution_dir(work_root: Path, execution_id: str = "exec-1") -> Path:
    run_dir = work_root / execution_id / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "message_end", "message": {"role": "assistant"}}) + "\n",
        encoding="utf-8",
    )
    (work_root / execution_id / "job" / "output.json").write_text("{}", encoding="utf-8")
    return work_root / execution_id


def _task(
    work_root: Path, kind: str = "process", execution_id: str = "exec-1", **kwargs
) -> UploadTask:
    defaults: dict = {
        "execution_id": execution_id,
        "lease_id": "lease-1",
        "execution_dir": work_root / execution_id,
        "node_key": "node_a",
        "status_fields": {
            "job_id": "job-1",
            "node_key": "node_a",
            "workspace_id": "ws-1",
            "agent_id": "agent",
            "run_dir": "run",
        },
        "kind": kind,
    }
    if kind == "process":
        defaults.update({"exit_code": 0, "expected_outputs": ("output.json",), "command": ("pi",)})
    defaults.update(kwargs)
    return UploadTask(**defaults)


def _queue(
    client: QueueFakeClient,
    stop: threading.Event | None = None,
    registry: BatchHeartbeatRegistry | None = None,
) -> UploadQueue:
    # #644：registry 不传 = legacy 单拍模式。registry 模式的用例必须经
    # heartbeat_registry 接线进 queue——_deliver_bulk 会用 queue 的 registry
    # 覆盖 task 的（executor.py 的生产接线同形），只在 task 上预置 registry
    # 测不到 registry 车道（退避 resume 用例曾因此空转）。
    return UploadQueue(
        client,
        ExecutionStatusReporter(None),
        max_concurrency=2,
        heartbeat_interval=0.05,
        stop=stop or threading.Event(),
        heartbeat_registry=registry,
    )


def _events_with_stderr(work_root: Path, stderr_lines: list[str]) -> None:
    """往既有 events.jsonl 前置非 JSON 行（spawn 侧 stderr 合并进 stdout 管道，
    pump 原样落进 events.jsonl——非 JSON 行就是 agent 的 stderr 文本）。"""
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    events = run_dir / "events.jsonl"
    events.write_text("\n".join([*stderr_lines, '{"type":"agent_end"}']) + "\n", encoding="utf-8")
