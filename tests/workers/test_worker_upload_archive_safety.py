"""#1165/#1168/#1169/#1174：Worker 归档安全收口（codex 评审 0.7.19 后收口轮）。

四条链路：
- 扫描失败防泄漏（#1165 P1）：pi_events 整趟失败时就地销毁未脱敏
  events.jsonl——队列级断言归档字节里没有密钥、结果照常上报；销毁失败
  （幸存者守卫）走诚实判败，raw 字节绝不进归档（pi_events 单元层用例见
  tests/executors/test_pi_event_json_redaction.py）。
- 失败臂可上报（#1168 P1）：execution_dir 被 agent 整目录自删时空归档
  写入自身会抛（在 except 之外）——bulk 车道异常退出、failed 结果报不上；
  不可写形态兜底 state 目录，结果仍可上报。
- 降级归档限内（#1169 P2）：metadata-only 归档受 claim 下发
  ``max_archive_bytes`` 约束，超限先裁非判定字段——重报不再吃 413、
  降级闸不再当终态删 marker 丢结果。
- 裁剪档序与回收口径（#1174）：command 清空后先按完整 error 复测（归因
  全文优先于 2048 档截断）；无上限（旧 Host / 旧 marker）的 413 回收按
  协议下限 ``MIN_RESULT_ARCHIVE_BYTES`` 裁剪（两个 0 值语义的分界钉子）。
"""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
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
from worker import state_evidence
from worker.upload import report_policy
from worker.upload.degraded_archive import (
    failed_metadata,
    write_empty_archive,
    write_metadata_only_archive,
)
from worker.upload.queue import UploadTask

pytestmark = pytest.mark.no_db

SECRET = "sk-live-supersecretgatewaytoken123"


@pytest.fixture
def evidence_root(tmp_path: Path) -> Path:
    root = state_evidence.configure_evidence_root(tmp_path / "state")
    try:
        yield root
    finally:
        state_evidence.reset_evidence_root()


class _ArchiveCapturingClient(QueueFakeClient):
    """report 时刻捕获归档字节与 run 目录残留（成功后目录即清）。"""

    def __init__(self) -> None:
        super().__init__()
        self.archives: list[bytes] = []
        self.events_survived: list[bool] = []

    def report(self, execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        self.archives.append(archive.read_bytes())
        events = archive.parent / "job" / "runs" / "node_a" / "worker" / "events.jsonl"
        try:
            self.events_survived.append(events.is_file() and events.stat().st_size > 0)
        except OSError:
            self.events_survived.append(False)
        return super().report(execution_id, lease_id, archive)


# -- #1165 P1：扫描失败 → 未脱敏 events 不进归档 ------------------------------


def test_scan_failure_discards_raw_events_from_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """队列级复现（修复前泄漏形态）：replace 抛错 → 扫描整趟失败返回
    None 形，prepare 忽略返回值继续 → tar 把含密钥的 events.jsonl 原样
    打包上传 Host。修复后：原文件就地截空、归档内 events 成员零字节、
    密钥字节不在归档里、结果照常上报（#959：扫描失败 = 归档降级但结果
    仍可上报，不是 failed 也不是丢结果）。"""
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", SECRET)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "tool_execution_end", "result": {"content": [{"text": SECRET}]}})
        + "\n",
        encoding="utf-8",
    )

    def failing_replace(self: Path, target: Path):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", failing_replace)
    client = _ArchiveCapturingClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "completed"  # 降级归档、正常上报
    raw = client.archives[0]
    assert SECRET.encode() not in raw  # 未脱敏 events 字节绝不进归档
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        events_members = [name for name in tar.getnames() if name.endswith("events.jsonl")]
    assert events_members, "run_dir 证据成员仍在（成员名保留）"
    assert not client.events_survived[0]  # 截空：非空未脱敏 events 不再幸存


def test_scan_failure_survivor_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """幸存者守卫（销毁自身失败的兜底）：非空未脱敏 events.jsonl 在扫描
    失败后仍在原位（EACCES 族）→ prepare 诚实判败走空归档——绝不让 raw
    字节进归档；取证臂照常转储（state 侧扫描未被破坏，红acted 副本留证）。"""
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", SECRET)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "tool_execution_end", "result": {"content": [{"text": SECRET}]}})
        + "\n",
        encoding="utf-8",
    )
    # 模拟「扫描失败 + pi_events 的就地销毁未能落地」：返回整趟失败形且原文件原样。
    monkeypatch.setattr(
        "worker.upload.prepare.scan_and_compress_pi_events",
        lambda *args, **kwargs: (None, 0, 0, b""),
    )
    client = _ArchiveCapturingClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"  # 诚实判败（#959：failed 是可上报的降级）
    assert "raw events file survived" in report["error_message"]
    raw = client.archives[0]
    assert SECRET.encode() not in raw  # 空归档 + result.json：无 events 字节
    incident = evidence_root / "exec-1__node_a"
    dumped = (incident / "events.jsonl").read_text(encoding="utf-8")  # 取证照常
    assert SECRET not in dumped


# -- #1168 P1：execution_dir 消失/不可写 → 失败臂仍可上报 ---------------------


def test_prep_failure_whole_execution_dir_gone_still_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """#1147 目标场景（修复前 F2 形态）：prepare 途中 execution_dir 整体
    消失 → 失败臂 write_empty_archive 因父目录不存在抛 FileNotFoundError
    ——在 except 之外，bulk 车道异常退出、failed 结果报不上、卡到租约
    过期重跑。修复后失败臂产出可上报的 failed 结果（归档存在且可解析）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)

    def vanish_and_boom(task: Any) -> None:
        shutil.rmtree(task.execution_dir, ignore_errors=True)
        raise FileNotFoundError(
            2,
            "No such file or directory",
            str(task.execution_dir / "job" / "runs" / "node_a" / "worker" / "events.jsonl"),
        )

    monkeypatch.setattr("worker.upload.prepare.prepare_result", vanish_and_boom)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    assert len(client.reports) == 1  # 修复前：0 条（失败臂自身抛错逃逸）
    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"].startswith("[work-dir-missing] result preparation failed:")
    assert f"evidence preserved at {evidence_root / 'exec-1__node_a'}" in report["error_message"]
    # 归档存在、可解析、可提交（fake 的 read_result_metadata 已断言 result.json 成员）。
    record = json.loads((evidence_root / "exec-1__node_a" / "incident.json").read_text("utf-8"))
    assert record["execution_dir_present"] is False


def test_prep_failure_parent_chain_gone_still_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """#1168 矩阵（父链形态）：work_root 整棵消失（execution_dir 连同其
    父目录）——mkdir(parents=True) 重建全链后失败臂照常产出可上报结果；
    取证侧 execution_dir 缺席与「execution_dir 消失」同形（listing skipped、
    incident.json 记 execution_dir_present=False）。修复前的模型只考虑了
    单层目录消失，父链整删是 agent ``rm -rf`` 更常见的真实形态。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)

    def rmtree_root_and_boom(task: Any) -> None:
        shutil.rmtree(work_root, ignore_errors=True)  # 父链整体消失（含 execution_dir 与 marker）
        raise FileNotFoundError(
            2,
            "No such file or directory",
            str(task.execution_dir / "job" / "runs" / "node_a" / "worker" / "events.jsonl"),
        )

    monkeypatch.setattr("worker.upload.prepare.prepare_result", rmtree_root_and_boom)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    assert len(client.reports) == 1  # 失败臂 I/O 不逃出 except：结果照常上报
    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"].startswith("[work-dir-missing] result preparation failed:")
    assert f"evidence preserved at {evidence_root / 'exec-1__node_a'}" in report["error_message"]
    record = json.loads((evidence_root / "exec-1__node_a" / "incident.json").read_text("utf-8"))
    assert record["execution_dir_present"] is False
    assert not (
        evidence_root / "exec-1__node_a" / "result.tar.gz"
    ).exists()  # 常规路径成功（无需 state 兜底）


def test_prep_failure_unwritable_execution_dir_strands_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """execution_dir 存在但不可写（EACCES 族，mkdir 救不了）→ 空归档兜底
    落进 state 取证结构（work_root 之外），结果仍上报成功；marker 收尾的
    unlink 失败只让 marker 滞留（重启 restore 重投吃 409 幂等），结果不丢。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    execution_dir = work_root / "exec-1"

    def lock_and_boom(task: Any) -> None:
        os.chmod(task.execution_dir, 0o500)  # 归档写入 EACCES：mkdir 无济于事
        raise RuntimeError("simulated failure with unwritable execution dir")

    monkeypatch.setattr("worker.upload.prepare.prepare_result", lock_and_boom)
    client = QueueFakeClient()
    queue = _queue(client)
    try:
        queue.submit(_task(work_root, exit_code=0))
        queue.shutdown()
    finally:
        os.chmod(execution_dir, 0o700)  # 让 tmp_path 收尾可清理

    assert len(client.reports) == 1
    assert client.reports[0]["status"] == "failed"
    stranded = evidence_root / "exec-1__node_a" / "result.tar.gz"
    assert stranded.is_file()  # 兜底归档落在 state 目录（work_root 之外）


# -- #1169 P2：metadata-only 归档受 claim 上限约束 ---------------------------


def test_metadata_only_archive_fits_declared_ceiling(tmp_path: Path) -> None:
    """复现（修复前 413 循环形态）：1 KiB 配置 + 4000 字符高熵
    error_message（gzip 近乎不可压缩）→ metadata-only 归档仍超限、未复测
    就覆写原归档 → Host 持续 413 → 降级闸第二次同形超限当终态删 marker、
    结果丢。修复后：裁剪非判定字段（command 清空、error_message 递减截断）
    到限内，status/exit_code 判定字段恒保留。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    task = _task(work_root, kind="prebuilt", command=("pi", "--flag"))
    message = secrets.token_hex(2000)  # 4000 字符高熵（gzip 不可压缩）
    metadata = failed_metadata(task, message)
    archive = work_root / "exec-1" / "result.tar.gz"

    write_metadata_only_archive(archive, metadata, max_bytes=1024)

    assert archive.stat().st_size <= 1024
    payload = read_result_metadata(archive)
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1
    assert payload["command"] == []  # 非判定字段让位
    trimmed = payload["error_message"]
    assert trimmed  # 归因头前缀保留（截断从头截，非空）
    assert message.startswith(trimmed)  # 前缀性质：截断只去尾


def test_metadata_only_archive_large_ceiling_trims_nothing(tmp_path: Path) -> None:
    """矩阵 {max=真实大上限}：64 MiB（默认量级）× 4000 字符高熵 error +
    高熵长 command——远在限内，零裁剪、载荷逐字段原样（含 command 全文）。
    与「未下发上限」用例的区别：这里走的是真下发值的限内判定路径。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    task = _task(work_root, kind="prebuilt", command=tuple(secrets.token_hex(8) for _ in range(64)))
    metadata = failed_metadata(task, secrets.token_hex(2000))
    archive = work_root / "exec-1" / "result.tar.gz"

    write_metadata_only_archive(archive, metadata, max_bytes=64 * 1024 * 1024)

    payload = read_result_metadata(archive)
    assert payload == metadata  # 逐字段原样：command / error_message 都未被裁


def test_metadata_only_archive_trims_command_before_error(tmp_path: Path) -> None:
    """矩阵 {max=1KiB × 高熵长 command × 短 error}：裁剪序先 command——
    command 清空后即落限内即收，error_message 全文保留（归因完整性优先于
    命令观测；状态机的字段优先级钉子）。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    task = _task(
        work_root, kind="prebuilt", command=tuple(secrets.token_hex(8) for _ in range(200))
    )
    metadata = failed_metadata(task, "short verdict")  # 短归因：不应被裁
    archive = work_root / "exec-1" / "result.tar.gz"

    write_metadata_only_archive(archive, metadata, max_bytes=1024)

    assert archive.stat().st_size <= 1024
    payload = read_result_metadata(archive)
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1
    assert payload["command"] == []  # 观测字段先裁
    assert payload["error_message"] == "short verdict"  # 归因字段全文保留


def test_metadata_only_archive_floor_form_keeps_verdict_fields(tmp_path: Path) -> None:
    """矩阵极限形态：天花板低于「判定字段 + tar/gz 开销」的地板（≈300B；
    生产配置下限 1 KiB 之上不可达，此用例是防回归钉）——裁剪序走完后仍
    超限，落盘最后形态（command 空、error_message 空）并记日志，写入永不
    失败；status / exit_code / output_artifacts 判定必需字段保留。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    task = _task(work_root, kind="prebuilt", command=("pi",))
    metadata = failed_metadata(task, "v" * 4000)
    archive = work_root / "exec-1" / "result.tar.gz"

    write_metadata_only_archive(archive, metadata, max_bytes=64)

    payload = read_result_metadata(archive)  # 归档存在、可解析（写入未失败）
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1
    assert payload["command"] == []
    assert payload["error_message"] == ""
    assert payload["output_artifacts"] == {}
    assert archive.stat().st_size > 64  # 超限形态如实落盘（极限形态不伪造体积）


def test_metadata_only_archive_without_ceiling_kept_verbatim(tmp_path: Path) -> None:
    """无上限（未下发 / 旧 Host）：判败载荷原样交付（不裁剪、不变小）——
    上限自适应只在真的有 claim 下发值时介入。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    task = _task(work_root, kind="prebuilt", command=("pi",))
    message = secrets.token_hex(2000)
    metadata = failed_metadata(task, message)
    archive = work_root / "exec-1" / "result.tar.gz"

    write_metadata_only_archive(archive, metadata)

    payload = read_result_metadata(archive)
    assert payload == metadata
    assert payload["error_message"] == message


def test_degrade_gate_413_recycle_uses_protocol_floor_despite_declared_ceiling(
    tmp_path: Path,
) -> None:
    """降级闸 413 臂的调用点接线（#1184 语义）：413 = claim 快照过期的
    判决信号——即使 task.max_archive_bytes 持久正值（4 KiB），回收也
    无条件按协议下限 1 KiB 裁（不复用旧值；4000 字符高熵归因裁到限内），
    重报归档对任何合法 Host 配置必可提交。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root)
    task.max_archive_bytes = 4 * 1024  # claim 时点快照：413 后已不可信
    archive = work_root / "exec-1" / "result.tar.gz"
    write_empty_archive(archive)
    gate = report_policy.ReportDegradeGate(task, archive)

    assert gate.on_rejection(413, secrets.token_hex(2000)) is True

    assert archive.stat().st_size <= MIN_RESULT_ARCHIVE_BYTES
    metadata = read_result_metadata(archive)
    assert metadata["status"] == "failed"
    assert metadata["error_message"]  # 前缀保留（判败归因可读）
    with tarfile.open(archive) as tar:
        assert tar.getnames() == [RESULT_METADATA_MEMBER]


def test_degrade_gate_413_recycle_without_ceiling_uses_protocol_floor(tmp_path: Path) -> None:
    """矩阵 {无上限 × 413 回收}：``max_archive_bytes == 0``（旧 Host / #1174
    前落盘的旧 marker）时回收按协议下限 ``MIN_RESULT_ARCHIVE_BYTES`` 裁剪
    ——Host 拒过 413 即证明它有上限，本地不知道具体值时按协议保证的最小
    上限裁，重报归档对任何合法 Host 配置必可提交。修复前 0 上限直接跳过
    裁剪（大 command 形态仍超限吃第二个 413）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root, command=tuple(secrets.token_hex(32) for _ in range(64)))
    archive = work_root / "exec-1" / "result.tar.gz"
    write_empty_archive(archive)
    gate = report_policy.ReportDegradeGate(task, archive)

    assert gate.on_rejection(413, secrets.token_hex(100)) is True

    assert archive.stat().st_size <= MIN_RESULT_ARCHIVE_BYTES
    metadata = read_result_metadata(archive)
    assert metadata["status"] == "failed"
    assert metadata["command"] == []  # 观测字段先让位（归因可读优先）


def test_degrade_gate_rewrite_failure_fallback_uses_protocol_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1174 二轮 P3-2：非 413 判决的换写失败回落位同受下限矩阵覆盖——
    0 值（旧 Host / #1174 前旧 marker）时回落产物按协议下限 1 KiB 裁剪。
    修复前回落传裸 0（跳过裁剪）：大 command 判败载荷重报吃 413，闸已置
    ``_archive_recycled`` 拒绝二次回收 → 终态删 marker 丢结果。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root, command=tuple(secrets.token_hex(32) for _ in range(64)))
    archive = work_root / "exec-1" / "result.tar.gz"
    write_empty_archive(archive)
    gate = report_policy.ReportDegradeGate(task, archive)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated embed rewrite failure")

    monkeypatch.setattr(report_policy, "embed_result_metadata", boom)

    assert gate.on_rejection(400, "HTTP 400: bad verdict") is True

    assert archive.stat().st_size <= MIN_RESULT_ARCHIVE_BYTES
    metadata = read_result_metadata(archive)
    assert metadata["status"] == "failed"
    assert metadata["command"] == []  # 观测字段让位（协议下限裁剪）


def test_degrade_gate_rewrite_failure_floors_restored_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1184 复审（恢复值 × 非 413 换写失败回落）：恢复任务读出的持久
    8 KiB 是可过期快照——回落回收按下限（不用持久值：Host 实际 1 KiB
    的下调形态下裁到 8 KiB 仍超限 → 重报 413 → 闸已回收拒绝二次 → 终态
    删 marker 丢结果）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    seed = _task(work_root, command=tuple(secrets.token_hex(32) for _ in range(64)))
    seed.max_archive_bytes = 8 * 1024
    task = UploadTask.from_json(
        json.loads(json.dumps(seed.to_json(), ensure_ascii=False)), work_root
    )
    archive = work_root / "exec-1" / "result.tar.gz"
    write_empty_archive(archive)
    gate = report_policy.ReportDegradeGate(task, archive)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated embed rewrite failure")

    monkeypatch.setattr(report_policy, "embed_result_metadata", boom)

    assert gate.on_rejection(400, "HTTP 400: bad verdict") is True

    assert archive.stat().st_size <= MIN_RESULT_ARCHIVE_BYTES
    metadata = read_result_metadata(archive)
    assert metadata["status"] == "failed"
    assert metadata["command"] == []  # 协议下限裁剪（非持久 8 KiB 口径）


def test_metadata_only_archive_keeps_full_error_after_command_trim(tmp_path: Path) -> None:
    """#1174 F2（裁剪档序）：「清空 command 后完整 error 落限」的形态——
    归因全文保留，不先进 2048 档截短。修复前 cap 序第一档先截 error，
    2500 字符高熵归因被无谓截掉尾部（错误归因信息不必要丢失），与档序
    声明的优先级（command 让位后先按完整 error 复测）相悖。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    command = tuple(secrets.token_hex(32) for _ in range(200))
    task = _task(work_root, kind="prebuilt", command=command)
    message = secrets.token_hex(1250)  # 2500 字符高熵（gzip 近乎不可压缩）
    metadata = failed_metadata(task, message)
    archive = work_root / "exec-1" / "result.tar.gz"

    # 上限形态：带 command 的全形态 ~12.8 KiB 必超 4096；清 command 后
    # 完整 error（2500 字符 + tar/gz 开销）本可落限内。
    write_metadata_only_archive(archive, metadata, max_bytes=4096)

    payload = read_result_metadata(archive)
    assert payload["command"] == []  # 观测字段先裁
    assert payload["error_message"] == message  # 归因全文：未被截到 2048
    assert archive.stat().st_size <= 4096
