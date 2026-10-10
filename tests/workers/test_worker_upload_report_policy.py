"""#959：结果上报的 Host 应答分级与诚实判败降级（worker/upload/report_policy.py）。

钉四件事：409 之外的 4xx 判决降级一次为 failed 上报（不再删 marker 后
等租约过期整次重跑）；5xx / 网络错误 / 408·425·429 从不降级、租约持有
期间持续重试直到 204 / 409；401 走认证丢失处置（#1082：保 marker、不
伪报 failed）；主路径 prepare 后按 Host 下发的 max_archive_bytes 预检，
超限直接判败而不送出注定 413 的归档。#1174 F1 再钉两维：上限随 marker
往返（恢复任务预检不失明）、无上限的 413 回收按协议下限裁剪。共享桩/
工具见 tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import json
import os
import secrets
import tarfile
from pathlib import Path
from typing import Any

import pytest

from shared.code_contract import MIN_RESULT_ARCHIVE_BYTES, RESULT_METADATA_MEMBER
from tests.workers.upload_queue_testlib import (
    QueueFakeClient,
    _execution_dir,
    _queue,
    _task,
    read_result_metadata,
)
from worker.upload import queue as upload_queue
from worker.upload import report_policy
from worker.upload.queue import PENDING_FILENAME, UploadTask

pytestmark = pytest.mark.no_db


class ScriptedReportClient(QueueFakeClient):
    """按脚本逐次回传状态码（脚本用尽后重复最后一个）；None = 抛瞬时
    RuntimeError（传输层单次尝试后上抛的形态，#1098）。每次 report 记下
    metadata 与归档成员，供断言降级载荷与归档形态（v2：metadata 从归档
    result.json 读回）。"""

    def __init__(self, script: list[int | None]) -> None:
        super().__init__()
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def report(self, execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        status = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        with tarfile.open(archive) as tar:
            members = tar.getnames()
        self.calls.append({"metadata": read_result_metadata(archive), "members": members})
        if status is None:
            raise RuntimeError("result report failed: exec-1: HTTP 503: b'unavailable'")
        return status, b"verdict body"


class _SizeGatedClient(QueueFakeClient):
    """按真实 Host 的归档大小闸行为回判（``agent_workers`` result 端点先验
    ``max_archive_bytes``）：归档超限回 413，限内收下并记一条 report。"""

    def __init__(self, ceiling: int) -> None:
        super().__init__()
        self.ceiling = ceiling
        # 只记录被收下的提交（413 的尝试在闸上被拒，不进本列表）。
        self.calls: list[dict[str, Any]] = []

    def report(self, execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        if archive.stat().st_size > self.ceiling:
            return 413, b"result archive over the configured ceiling"
        with tarfile.open(archive) as tar:
            members = tar.getnames()
        self.calls.append({"metadata": read_result_metadata(archive), "members": members})
        return super().report(execution_id, lease_id, archive)


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_queue, "_RETRY_BASE_SECONDS", 0.001)
    monkeypatch.setattr(upload_queue, "_RETRY_CAP_SECONDS", 0.001)


def _deliver(work_root: Path, client: QueueFakeClient, **task_kwargs: Any) -> None:
    queue = _queue(client)
    queue.submit(_task(work_root, **task_kwargs))
    queue.shutdown()


@pytest.mark.parametrize("status", [400, 422])
def test_verdict_4xx_degrades_to_failed_report_once(tmp_path: Path, status: int) -> None:
    """修复前：非 204/401/409 一律删 marker 丢结果，Host 永远收不到终态，租约
    过期后重排、整次执行重跑。修复后降级一次为诚实判败（证据成员随归档
    保留、result.json 换写为判败载荷），Host 记录显式失败，目录照常收尾。"""
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
    # 非 413 判决：证据成员保留（events 仍随判败上报），metadata 成员换写。
    assert any(name.endswith("events.jsonl") for name in client.calls[1]["members"])
    assert RESULT_METADATA_MEMBER in client.calls[1]["members"]
    assert not (work_root / "exec-1").exists()


def test_401_is_auth_loss_not_run_verdict(tmp_path: Path) -> None:
    """#1082：401 = 认证丢失处置——token 失效是 Worker 级事实（Host 全端点
    同一 token 鉴权），不是本次 run 的判决。不降级、不伪报 failed、不删
    marker（aborted 形态：重注册后 restore 重投，由租约归属决定重报或
    409 终态）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([401, 204])
    _deliver(work_root, client)

    # 一次 401 后即放弃本 attempt：不重报、不降级（第二个 204 永不被消费）。
    assert len(client.calls) == 1
    assert client.calls[0]["metadata"]["status"] == "completed"
    # marker 与执行目录保留：重启 restore 重新投递。
    assert (work_root / "exec-1" / PENDING_FILENAME).is_file()
    assert (work_root / "exec-1").is_dir()


def test_413_degrade_ships_a_failed_metadata_only_archive(tmp_path: Path) -> None:
    """413 = 归档本身不可提交：降级判败必须回收成仅含判败 metadata 的
    result.json 归档（v2：无成员的空归档不再合法——缺 result.json 即
    Host 400，判败载荷必须随归档交付）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([413, 204])
    _deliver(work_root, client)

    assert len(client.calls) == 2
    assert client.calls[1]["metadata"]["status"] == "failed"
    assert "HTTP 413" in client.calls[1]["metadata"]["error_message"]
    assert client.calls[1]["members"] == [RESULT_METADATA_MEMBER]
    assert not (work_root / "exec-1").exists()


def test_degraded_report_rejected_again_is_terminal(tmp_path: Path) -> None:
    """降级闸只开一次：判败上报也被 4xx 拒收即终态（删 marker），不无限重报。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([400])
    _deliver(work_root, client)

    assert len(client.calls) == 2
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()


def test_413_after_metadata_verdict_degrades_once_more_with_metadata_only_archive(
    tmp_path: Path,
) -> None:
    """codex R2 P2：Host 先验归档大小再回判决。未下发
    max_archive_bytes（旧 Host / 崩溃恢复）时首个 400 降级保留证据归档，
    判败重报才吃到 413。修复前闸已关、删 marker 退化为租约过期重跑；
    修复后回收成 metadata-only 归档再报一次，Host 收到显式判败。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([400, 413, 204])
    _deliver(work_root, client)

    assert len(client.calls) == 3
    assert client.calls[1]["members"], "首次（非 413）降级保留归档作证据"
    final = client.calls[2]
    assert final["metadata"]["status"] == "failed"
    # 判败原因保持首个判决（真实失败原因），不被 413 覆盖。
    assert "HTTP 400" in final["metadata"]["error_message"]
    assert final["members"] == [RESULT_METADATA_MEMBER]
    assert not (work_root / "exec-1").exists()


@pytest.mark.parametrize("script", [[400, 413, 413], [413, 413], [400, 413, 400]])
def test_degrade_gate_stays_bounded_after_recycled_archive(
    tmp_path: Path, script: list[int]
) -> None:
    """413 二次降级只在归档尚未回收时开放：metadata-only 归档仍被拒即终态，
    重报有界。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient(script)
    _deliver(work_root, client)

    assert len(client.calls) == len(script)
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()


def test_lease_conflict_409_never_degrades(tmp_path: Path) -> None:
    """409 = 租约已不归本 attempt（含提交已落地后的重报）：不降级、不重报。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([409])
    _deliver(work_root, client)

    assert len(client.calls) == 1
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()


@pytest.mark.parametrize("transient", [None, 500, 503, 408, 425, 429])
def test_transient_failures_never_degrade_and_retry_until_409(
    tmp_path: Path, transient: int | None
) -> None:
    """5xx / 网络错误 / 408·425·429 从不把可交付结果判败（对抗复审 B1）：
    Host 存活而 /result 持续失败时，判败等于把长时成功执行白跑。租约持有期间
    持续退避重试，每次重报都是原结果，由 409（租约已不归本 attempt）自然终止。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([transient] * 30 + [409])
    _deliver(work_root, client)

    assert len(client.calls) == 31
    assert all(call["metadata"]["status"] == "completed" for call in client.calls)
    assert all("output.json" in call["members"] for call in client.calls)
    assert not (work_root / "exec-1" / PENDING_FILENAME).exists()


def test_transient_recovery_delivers_original(tmp_path: Path) -> None:
    """瞬时失败后恢复：原结果照常交付，不降级。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = ScriptedReportClient([503, None, 429, 204])
    _deliver(work_root, client)

    assert [call["metadata"]["status"] for call in client.calls] == ["completed"] * 4
    assert not (work_root / "exec-1").exists()


def test_retryable_client_statuses_are_not_verdicts() -> None:
    for status in (408, 425, 429, 500, 503):
        assert report_policy.is_transient_status(status)
        assert not report_policy.is_verdict_rejection(status)
    for status in (400, 413, 422):
        assert report_policy.is_verdict_rejection(status)
    assert not report_policy.is_verdict_rejection(409)
    # #1082：401 分流认证处置，不进判败降级臂。
    assert not report_policy.is_verdict_rejection(401)


def test_main_path_archive_over_declared_ceiling_fails_honestly(tmp_path: Path) -> None:
    """主路径 prepare 预检：内嵌产物的归档超 Host 下发上限时，修复前送出
    注定 413 的归档、被当终态删 marker → 重跑 → 再打包同样大的归档。修复后
    prepare 阶段即回收判败，只上报一次，零产物上传（v2：判败载荷随
    result.json 单成员归档交付）。"""
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
    assert client.calls[0]["members"] == [RESULT_METADATA_MEMBER]
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


def test_413_recycle_ignores_stale_persisted_ceiling(tmp_path: Path) -> None:
    """#1184：413 = claim 快照过期的判决信号——即使 marker 持久化了正值
    （8 KiB；Host claim 后重启把 max_archive_bytes 下调到 1 KiB 的形态），
    413 回收也不复用旧值，无条件按协议下限裁。修复前 ``or`` 复用持久
    正值：回收产物裁进已失效的口径（4 KiB 级 command 形态仍超 Host 实际
    1 KiB）→ 重报吃第二个 413 → 闸判终态删 marker，Host 从未记录结果。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    command = tuple(secrets.token_hex(32) for _ in range(64))
    # Host 实际上限 1 KiB；marker 持久的是 claim 时点的旧 8 KiB。
    client = _SizeGatedClient(MIN_RESULT_ARCHIVE_BYTES)
    queue = _queue(client)
    queue.submit(_task(work_root, max_archive_bytes=8 * 1024, command=command))
    queue.shutdown()

    assert len(client.reports) == 1  # 按协议下限裁剪的判败重报被 Host 收下
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "HTTP 413" in report["error_message"]
    assert report["command"] == []  # 观测字段让位（协议下限裁剪）
    assert not (work_root / "exec-1").exists()  # delivered 收尾（非终态丢弃）


def test_restored_task_defers_ceiling_to_host_verdict(tmp_path: Path) -> None:
    """#1184 复审（预检误杀，端到端判别）：任务按旧 8 KiB 上限领取 → marker
    持久化 8 KiB → Worker 崩溃 → Host 重启把 max_archive_bytes 调大到
    64 KiB → 恢复任务读出 8 KiB 预检 → 16 KiB 的、Host 现在完全能收的归档
    被本地回收判 failed（成功执行被永久误杀）。修复后恢复任务的持久值
    不参与预检（快照双向可过期——下调由 413 兜底、上调只能交 Host 判决）：
    归档照发，Host 收下 completed。"""
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    (execution_dir / "job" / "output.json").write_bytes(os.urandom(16 * 1024))
    # Host 实际（重启后）上限 64 KiB；marker 持久的是领取时点的旧 8 KiB。
    client = _SizeGatedClient(64 * 1024)
    queue = _queue(client)
    task = _task(work_root, max_archive_bytes=8 * 1024)
    restored = UploadTask.from_json(
        json.loads(json.dumps(task.to_json(), ensure_ascii=False)), work_root
    )
    queue.submit(restored)
    queue.shutdown()

    assert len(client.calls) == 1
    report = client.calls[0]["metadata"]
    assert report["status"] == "completed"  # 修复前：本地预检误杀为 failed
    assert "output.json" in client.calls[0]["members"]  # 归档照发（未被回收）
    assert client.calls[0]["members"][0] == RESULT_METADATA_MEMBER
    assert not (work_root / "exec-1").exists()


def test_online_task_precheck_still_fails_over_declared_ceiling(tmp_path: Path) -> None:
    """在线任务的 claim 值可信（Host 刚随 claim 下发）——同一 8 KiB 上限
    × 16 KiB 归档的形态，在线预检照常本地诚实判败（现行为回归钉住，
    与上一用例的恢复形态互为判别对）。"""
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    (execution_dir / "job" / "output.json").write_bytes(os.urandom(16 * 1024))
    client = ScriptedReportClient([204])
    _deliver(work_root, client, max_archive_bytes=8 * 1024)

    assert len(client.calls) == 1
    report = client.calls[0]["metadata"]
    assert report["status"] == "failed"
    assert "over the 8192-byte Host archive ceiling" in report["error_message"]
    assert client.calls[0]["members"] == [RESULT_METADATA_MEMBER]


def test_restored_task_413_recycle_floors_to_protocol_minimum(tmp_path: Path) -> None:
    """#1184 用例回归（恢复 × 413 交叠）：恢复任务（持久 8 KiB）的归档
    超过 Host 实际 1 KiB → 413 → 回收臂按协议下限裁（579dece4a：413 使
    快照失效——持久值不参与回收口径），判败重报被 Host 收下。本修复
    前后均应通过：预检跳过只是让归档多走一跳，413 兜底口径不变。"""
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    (execution_dir / "job" / "output.json").write_bytes(os.urandom(4 * 1024))
    command = tuple(secrets.token_hex(32) for _ in range(64))
    client = _SizeGatedClient(MIN_RESULT_ARCHIVE_BYTES)
    queue = _queue(client)
    task = _task(work_root, max_archive_bytes=8 * 1024, command=command)
    restored = UploadTask.from_json(
        json.loads(json.dumps(task.to_json(), ensure_ascii=False)), work_root
    )
    queue.submit(restored)
    queue.shutdown()

    assert len(client.reports) == 1  # 原报 413（不记条目）+ 按下限裁剪的判败重报
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "HTTP 413" in report["error_message"]
    assert report["command"] == []  # 观测字段让位（协议下限裁剪）
    assert not (work_root / "exec-1").exists()


def test_413_recycle_without_ceiling_trims_to_protocol_floor(tmp_path: Path) -> None:
    """#1174 F1（语义层，端到端）：无 claim 上限（旧 Host / 旧 marker）的
    413 回收臂按协议下限 ``MIN_RESULT_ARCHIVE_BYTES`` 裁剪——Host 拒过
    413 即证明它有上限，本地按协议保证的最小上限（1 KiB）裁，重报归档对
    任何合法 Host 配置必可提交。修复前 0 上限跳过裁剪：大 command 的
    metadata-only 归档仍超 Host 的 1 KiB 配置 → 第二个 413 → 闸判终态删
    marker，Host 从未记录结果、租约重跑。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    command = tuple(secrets.token_hex(32) for _ in range(64))
    client = _SizeGatedClient(MIN_RESULT_ARCHIVE_BYTES)
    queue = _queue(client)
    queue.submit(_task(work_root, max_archive_bytes=0, command=command))
    queue.shutdown()

    assert len(client.reports) == 1  # 裁剪后的判败重报被 Host 收下
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "HTTP 413" in report["error_message"]
    assert report["command"] == []  # 观测字段让位（协议下限裁剪）
    assert not (work_root / "exec-1").exists()  # delivered 收尾（非终态丢弃）
