"""#959：结果上报的 Host 应答分级与诚实判败降级（worker/upload/report_policy.py）。

钉三件事：409 之外的 4xx 判决降级一次为 failed 上报（不再删 marker 后
等租约过期整次重跑）；5xx / 网络错误有界重试、耗尽后降级、判败也耗尽则
保留 marker 交给下次启动；主路径 prepare 后按 Host 下发的
max_archive_bytes 预检，超限直接判败而不送出注定 413 的归档。
共享桩/工具见 tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path
from typing import Any

import pytest

from tests.workers.upload_queue_testlib import (
    QueueFakeClient,
    _execution_dir,
    _queue,
    _task,
)
from worker.upload import queue as upload_queue
from worker.upload import report_policy
from worker.upload.queue import PENDING_FILENAME

pytestmark = pytest.mark.no_db


class ScriptedReportClient(QueueFakeClient):
    """按脚本逐次回传状态码（脚本用尽后重复最后一个）；None = 抛瞬时
    RuntimeError（传输层内层重试耗尽的形态）。每次 report 记下 metadata
    与归档成员，供断言降级载荷与归档形态。"""

    def __init__(self, script: list[int | None]) -> None:
        super().__init__()
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def report(
        self, execution_id: str, lease_id: str, metadata: dict, archive: Path
    ) -> tuple[int, bytes]:
        status = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        with tarfile.open(archive) as tar:
            members = tar.getnames()
        self.calls.append({"metadata": dict(metadata), "members": members})
        if status is None:
            raise RuntimeError("result report failed: exec-1: HTTP 503: b'unavailable'")
        return status, b"verdict body"


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_queue, "_RETRY_BASE_SECONDS", 0.001)
    monkeypatch.setattr(upload_queue, "_RETRY_CAP_SECONDS", 0.001)
    monkeypatch.setattr(report_policy, "REPORT_TRANSIENT_MAX_ROUNDS", 3)


def _deliver(work_root: Path, client: QueueFakeClient, **task_kwargs: Any) -> None:
    queue = _queue(client)
    queue.submit(_task(work_root, **task_kwargs))
    queue.shutdown()


@pytest.mark.parametrize("status", [400, 401, 422])
def test_verdict_4xx_degrades_to_failed_report_once(tmp_path: Path, status: int) -> None:
    """修复前：非 204/409 一律删 marker 丢结果，Host 永远收不到终态，租约
    过期后重排、整次执行重跑。修复后降级一次为诚实判败（归档保留作证据），
    Host 记录显式失败，目录照常收尾。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([status, 204])
    _deliver(work_root, client)

    assert len(client.calls) == 2
    assert client.calls[0]["metadata"]["status"] == "completed"
    degraded = client.calls[1]["metadata"]
    assert degraded["status"] == "failed"
    assert f"result report rejected by Host: HTTP {status}" in degraded["error_message"]
    assert degraded["output_artifacts"] == {}
    # 非 413 判决：归档原样保留（events 证据仍随判败上报）。
    assert any(name.endswith("events.jsonl") for name in client.calls[1]["members"])
    assert not (work_root / "exec-1").exists()


def test_413_degrade_ships_an_empty_archive(tmp_path: Path) -> None:
    """413 = 归档本身不可提交：降级判败必须换成空归档，否则重报同样 413。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([413, 204])
    _deliver(work_root, client)

    assert len(client.calls) == 2
    assert client.calls[1]["metadata"]["status"] == "failed"
    assert "HTTP 413" in client.calls[1]["metadata"]["error_message"]
    assert client.calls[1]["members"] == []
    assert not (work_root / "exec-1").exists()


def test_degraded_report_rejected_again_is_terminal(tmp_path: Path) -> None:
    """降级闸只开一次：判败上报也被 4xx 拒收即终态（删 marker），不无限重报。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([400])
    _deliver(work_root, client)

    assert len(client.calls) == 2
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()


def test_lease_conflict_409_never_degrades(tmp_path: Path) -> None:
    """409 = 租约已不归本 attempt（含提交已落地后的重报）：不降级、不重报。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([409])
    _deliver(work_root, client)

    assert len(client.calls) == 1
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()


@pytest.mark.parametrize("transient", [None, 500, 503])
def test_transient_failures_are_bounded_then_degrade(tmp_path: Path, transient: int | None) -> None:
    """5xx / 网络错误有界重试（此处上限 3 轮）：耗尽后降级为诚实判败（空
    归档——内容本身可能就是 Host 端失败原因），Host 恢复后判败被接收。
    重试幂等由 Host 的租约绑定提交保证（重报要么首次提交、要么 409）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([transient, transient, transient, 204])
    _deliver(work_root, client)

    assert len(client.calls) == 4
    assert all(call["metadata"]["status"] == "completed" for call in client.calls[:3])
    degraded = client.calls[3]["metadata"]
    assert degraded["status"] == "failed"
    assert "result report failed after 3 attempts" in degraded["error_message"]
    assert client.calls[3]["members"] == []
    assert not (work_root / "exec-1").exists()


def test_transient_recovery_within_budget_delivers_original(tmp_path: Path) -> None:
    """预算内恢复：原结果照常交付，不降级。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([503, None, 204])
    _deliver(work_root, client)

    assert [call["metadata"]["status"] for call in client.calls] == ["completed"] * 3
    assert not (work_root / "exec-1").exists()


def test_host_unreachable_gives_up_keeping_marker(tmp_path: Path) -> None:
    """判败上报也耗尽（Host 不可达）：放弃本轮投递但保留 marker——下次启动
    restore 重新 prepare 原结果再投；总尝试数有界（2 × 上限）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([None])
    _deliver(work_root, client)

    assert len(client.calls) == 6
    assert (work_root / "exec-1" / PENDING_FILENAME).is_file()


def test_main_path_archive_over_declared_ceiling_fails_honestly(tmp_path: Path) -> None:
    """主路径 prepare 预检：内嵌产物的归档超 Host 下发上限时，修复前送出
    注定 413 的归档、被当终态删 marker → 重跑 → 再打包同样大的归档。修复后
    prepare 阶段即回收空归档诚实判败，只上报一次，零产物上传。"""
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    (execution_dir / "job" / "output.json").write_bytes(os.urandom(64 * 1024))
    client = ScriptedReportClient([204])
    _deliver(work_root, client, max_archive_bytes=16 * 1024)

    assert len(client.calls) == 1
    report = client.calls[0]["metadata"]
    assert report["status"] == "failed"
    assert "over the 16384-byte Host archive ceiling" in report["error_message"]
    assert report["output_artifacts"] == {}
    assert client.calls[0]["members"] == []
    assert client.uploads == {}
    assert not (work_root / "exec-1").exists()


def test_main_path_precheck_skipped_without_declared_ceiling(tmp_path: Path) -> None:
    """未下发上限（旧 Host / 崩溃恢复的任务）时本地不猜 64 MiB 默认——可能
    低于 Host 实际配置而误杀可交付结果；交给 Host 判决（413 走降级臂）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([204])
    _deliver(work_root, client, max_archive_bytes=0)

    assert len(client.calls) == 1
    assert client.calls[0]["metadata"]["status"] == "completed"
    assert "output.json" in client.calls[0]["members"]
