"""#755 codex P1 Host 侧 commit 层测试：清单走归档成员的读回与诚实判败。

Worker 结果头溢出时把完整直传 ref 清单写成归档首成员
``result-output-artifacts.json``，头里只带 ``output_artifacts_in_archive``
标记；commit 层（agent_result_commit）见标记即在 finish 前读回清单并
enrich outcome/record。本文件用 stub broker/completion 钉住该编排：
finish 收到的 outcome 已是全集（空清单翻转、HEAD 校验、staged promote
的既有路径由此零改动），读回失败的各形态诚实改判 failed（cancelled
例外——取消语义不翻转）。
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from server.app.agent_broker.agent_result_commit import commit_agent_result
from server.app.agent_broker.result_output_manifest import load_archived_output_artifacts
from server.app.agent_control.completion import AgentOutcome
from shared.code_contract import RESULT_OUTPUT_ARTIFACTS_MEMBER

pytestmark = pytest.mark.no_db  # 全 stub 编排，不触库

_REF = {
    "storage_key": "jobs-staging/ws-1/job-1/exec-1/out.json",
    "size_bytes": 3,
    "content_hash": "a" * 64,
}


class _StubBroker:
    def __init__(self, bundle_dir: Path) -> None:
        self.bundle_dir = bundle_dir
        self.database_dsn = None
        self.done_records: list[dict[str, Any]] = []

    def claimed_payload(self, execution_id: str, worker_id: str) -> dict[str, Any]:
        return {"lease_id": "lease-1", "job_id": "job-1", "node_key": "node_a", "manifest": {}}

    def mark_done(self, execution_id: str, worker_id: str, lease_id: str, record: dict) -> dict:
        self.done_records.append(record)
        return {"ok": True}

    def discard_result_archive(self, archive_name: str) -> None:
        if self.bundle_dir is not None:
            (self.bundle_dir / archive_name).unlink(missing_ok=True)

    def retire_bundle(self, bundle_name: str) -> None:
        pass


class _StubCompletion:
    def __init__(self) -> None:
        self.finished: list[dict[str, Any]] = []

    def finish(self, **kwargs: Any) -> bool:
        self.finished.append(kwargs)
        return True


def _archive(tmp_path: Path, manifest: dict[str, Any] | None) -> Path:
    """构造结果归档：有清单时清单为首成员（Worker 写入形态），后随一个
    普通成员（证明流式扫描命中即停）。"""
    archive = tmp_path / "staged.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        if manifest is not None:
            payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo(RESULT_OUTPUT_ARTIFACTS_MEMBER)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        tail = b"events\n"
        info = tarfile.TarInfo("runs/node_a/worker/events.jsonl")
        info.size = len(tail)
        tar.addfile(info, io.BytesIO(tail))
    return archive


def _commit(
    tmp_path: Path,
    archive: Path,
    *,
    status: str = "completed",
    error_message: str = "",
    exit_code: int | None = None,
) -> tuple[_StubBroker, _StubCompletion, dict[str, Any]]:
    broker = _StubBroker(tmp_path / "bundles")
    broker.bundle_dir.mkdir()
    completion = _StubCompletion()
    record: dict[str, Any] = {
        "status": status,
        "exit_code": (0 if status == "completed" else 1) if exit_code is None else exit_code,
        "error_message": error_message,
        "output_artifacts": {},
        "output_artifacts_in_archive": True,
    }
    outcome = AgentOutcome(  # type: ignore[arg-type]
        status=status, exit_code=record["exit_code"], error_message=error_message
    )
    commit_agent_result(
        broker,  # type: ignore[arg-type]
        completion,  # type: ignore[arg-type]
        "exec-1",
        "worker-1",
        "lease-1",
        outcome,
        record,
        archive,
    )
    return broker, completion, record


def test_commit_enriches_outcome_from_archived_manifest(tmp_path: Path) -> None:
    """marker + 归档含清单 → finish 收到的 outcome.output_artifacts 是清单
    全集（登记走既有 finish 路径），record 同步（审计面）。"""
    manifest = {"out.json": dict(_REF), "logs/trace.json": {**_REF, "size_bytes": 7}}
    broker, completion, record = _commit(tmp_path, _archive(tmp_path, manifest))

    finished = completion.finished[0]["outcome"]
    assert finished.output_artifacts == manifest
    assert broker.done_records[0]["output_artifacts"] == manifest
    assert record["output_artifacts"] == manifest
    # #755 对抗复审 F2：commit 后归档即回收，「清单在归档里」在持久化面上
    # 永不再真——标记键归一为 False，record 自洽。
    assert record["output_artifacts_in_archive"] is False


def test_commit_missing_manifest_member_fails_honestly(tmp_path: Path) -> None:
    """标记在、清单成员不在 → run 诚实 failed（沿用空清单翻转语义钉子：
    产物引用不可用不得按 completed 提交），record 同步翻转。"""
    broker, completion, record = _commit(tmp_path, _archive(tmp_path, None))

    finished = completion.finished[0]["outcome"]
    assert finished.status == "failed"
    assert finished.exit_code == 1
    assert "manifest is unreadable" in finished.error_message
    assert finished.output_artifacts == {}
    assert record["status"] == "failed"
    assert record["exit_code"] == 1
    assert record["output_artifacts"] == {}
    assert record["output_artifacts_in_archive"] is False
    assert "manifest is unreadable" in broker.done_records[0]["error_message"]


def test_commit_failed_run_keeps_original_diagnosis_on_bad_manifest(tmp_path: Path) -> None:
    """#755 对抗复审 F1：already-failed 的 run 遇上坏清单——原始失败签名
    （exit code + 诊断）保留，读回失败只追加说明，不得整体覆盖。"""
    broker, completion, record = _commit(
        tmp_path,
        _archive(tmp_path, None),
        status="failed",
        error_message="Agent process exited 137: OOM killed",
        exit_code=137,
    )

    finished = completion.finished[0]["outcome"]
    assert finished.status == "failed"
    assert finished.exit_code == 137
    assert "OOM killed" in finished.error_message
    assert "manifest is unreadable" in finished.error_message
    assert record["status"] == "failed"
    assert record["exit_code"] == 137
    assert record["error_message"] == finished.error_message
    assert record["output_artifacts"] == {}
    assert record["output_artifacts_in_archive"] is False
    assert broker.done_records[0]["error_message"] == finished.error_message


def test_commit_oversized_manifest_fails_honestly(tmp_path: Path) -> None:
    """清单条目数超 128（与头部清单同上限）→ ValueError → 诚实判败。"""
    manifest = {f"out-{i:03d}.json": dict(_REF) for i in range(129)}
    _, completion, _ = _commit(tmp_path, _archive(tmp_path, manifest))

    finished = completion.finished[0]["outcome"]
    assert finished.status == "failed"
    assert "manifest is unreadable" in finished.error_message


def test_commit_bad_ref_in_manifest_fails_honestly(tmp_path: Path) -> None:
    """清单里的坏 ref（非 CAS 形态、非合法直传形态）→ 诚实判败。"""
    _, completion, _ = _commit(tmp_path, _archive(tmp_path, {"out.json": "md5:deadbeef"}))

    finished = completion.finished[0]["outcome"]
    assert finished.status == "failed"
    assert "manifest is unreadable" in finished.error_message


def test_commit_cancelled_with_bad_manifest_keeps_cancelled(tmp_path: Path) -> None:
    """cancelled + 坏清单 → 状态不翻转（取消语义优先），清单面丢弃；
    #755 对抗复审 F3：partial ref 全集随归档回收丢失，record 必须留痕。"""
    _, completion, record = _commit(
        tmp_path,
        _archive(tmp_path, {"out.json": "md5:deadbeef"}),
        status="cancelled",
        error_message="Agent Worker is shutting down",
    )

    finished = completion.finished[0]["outcome"]
    assert finished.status == "cancelled"
    assert finished.exit_code == 1
    assert "shutting down" in finished.error_message
    assert record["status"] == "cancelled"
    assert record["output_artifacts"] == {}
    assert "manifest is unreadable" in record["error_message"]
    assert record["output_artifacts_in_archive"] is False


def test_load_archived_output_artifacts_rejects_unsafe_name(tmp_path: Path) -> None:
    """读回面的名字安全校验：绝对路径与 .. 形态拒绝（Worker 归档不可信）。"""
    for bad in ("/etc/passwd", "../escape.json"):
        with pytest.raises(ValueError, match="unsafe archived output artifact name"):
            load_archived_output_artifacts(_archive(tmp_path, {bad: dict(_REF)}))
