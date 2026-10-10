"""#1174 F3：marker 删不掉的终态收口（worker/upload 的 delivery tombstone）。

场景（codex 二轮，已核实）：EACCES/EROFS 使 execution_dir 不可写——归档
兜底滞留 state 目录取证结构后 report 204 成功，``drop_marker`` 的
``marker.unlink()`` 在不可写目录上抛 OSError 逃出 report 车道（aborted），
marker 滞留；重启 restore 重投 → Host 409（终态幂等拒绝）→ 清理又在同一
点失败——每重启一轮的无限恢复噪声。

收口状态机（marker 可删性 × Host 终态应答，钉在 worker/upload/
delivery_tombstone.py 与 cleanup.drop_marker）：

- marker 可删（常规）：204 / 409 / 判决终态后 marker 即删，restore 不再
  见，零噪声。
- marker 不可删（EACCES/EROFS 族）：终态收尾已完成、本机无法用 marker 表达
  「已收尾」→ 在 state 侧 incident 目录写 ``upload-delivered.json``
  tombstone（键 execution_id + lease_id，另记 outcome：delivered=Host 已
  收下 / rejected=判决终态未收下 / lost=租约死——三者重投都只会重放幂等
  应答或同一判决），restore 读到即跳过重投、只做 best-effort 目录清理。
  - 204：重投本会吃 Host 幂等 204（重复提交侧无危害）——跳过同样正确。
  - 409：重投吃确定性 409，无数据风险但重复才是噪声——跳过即归零。
  - tombstone 自身写失败（state 目录也不可写）：退回既有形态（每重启
    一次有界重投、吃一次幂等判决），不可能更差。

queue 级端到端用例见本文件；marker 字段往返（F1 持久化层）在
tests/workers/test_upload_pending_marker.py。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.workers.upload_queue_testlib import (
    QueueFakeClient,
    _execution_dir,
    _queue,
    _task,
)
from worker import state_evidence
from worker.execution.ownership import write_owner_marker
from worker.upload.cleanup import drop_marker
from worker.upload.constants import PENDING_FILENAME

pytestmark = pytest.mark.no_db


@pytest.fixture
def evidence_root(tmp_path: Path) -> Path:
    root = state_evidence.configure_evidence_root(tmp_path / "state")
    try:
        yield root
    finally:
        state_evidence.reset_evidence_root()


def _write_marker(work_root: Path) -> None:
    task = _task(work_root)
    (work_root / "exec-1" / PENDING_FILENAME).write_text(
        json.dumps(task.to_json(), ensure_ascii=False), encoding="utf-8"
    )


def test_drop_marker_unlink_failure_records_tombstone_without_raising(
    tmp_path: Path, evidence_root: Path
) -> None:
    """修复前形态：unlink 在不可写目录上抛 OSError 逃出 report 车道（结果
    已交付却被当 aborted）。修复后不抛——state 侧记 tombstone（含 outcome：
    调用点交付形态 delivered / 判决丢弃 rejected / 租约死 lost），返回 False
    （marker 滞留但已被 tombstone 置为 inert）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_marker(work_root)
    task = _task(work_root)
    os.chmod(task.execution_dir, 0o500)  # unlink EACCES：删 marker 需目录写权
    try:
        assert drop_marker(task, "delivered") is False
    finally:
        os.chmod(task.execution_dir, 0o700)

    tombstone = evidence_root / "exec-1__node_a" / "upload-delivered.json"
    assert tombstone.is_file()
    record = json.loads(tombstone.read_text(encoding="utf-8"))
    assert record["execution_id"] == "exec-1"
    assert record["lease_id"] == "lease-1"
    assert record["outcome"] == "delivered"  # 204 已交付：与判决丢弃可区分
    assert (task.execution_dir / PENDING_FILENAME).is_file()  # 滞留（不可删）


def test_drop_marker_tombstone_write_failure_degrades_open(
    tmp_path: Path, evidence_root: Path
) -> None:
    """state 目录同样不可写（tombstone 写不进）：fail-open 到既有形态——
    仅记日志、不抛（每重启一次有界重投），交付语义不受影响。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_marker(work_root)
    task = _task(work_root)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o500)
    os.chmod(task.execution_dir, 0o500)
    try:
        assert drop_marker(task, "rejected") is False
    finally:
        os.chmod(task.execution_dir, 0o700)
        os.chmod(state_dir, 0o700)

    assert not (evidence_root / "exec-1__node_a").exists()


@pytest.mark.parametrize("report_status", [204, 409])
def test_restore_skips_marker_with_delivered_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path, report_status: int
) -> None:
    """端到端（修复前无限循环形态）：不可写 execution_dir → 归档滞留 state
    → report 终态（204 已提交 / 409 幂等拒绝）→ marker 删不掉。修复前
    第二次 restore 照样重投（每重启一轮 report + 清理失败）；修复后
    tombstone 命中 → 零重投、零重报，目录 best-effort 清理。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)

    def lock_and_boom(task: Any) -> None:
        os.chmod(task.execution_dir, 0o500)  # 归档写入 EACCES：兜底滞留 state
        raise RuntimeError("simulated failure with unwritable execution dir")

    monkeypatch.setattr("worker.upload.prepare.prepare_result", lock_and_boom)
    client = QueueFakeClient(report_status=report_status)
    queue = _queue(client)
    try:
        queue.submit(_task(work_root, exit_code=0))
        queue.shutdown()
    finally:
        os.chmod(work_root / "exec-1", 0o700)

    assert len(client.reports) == 1  # 终态已送达 Host（204 提交 / 409 幂等拒绝）
    assert (work_root / "exec-1" / PENDING_FILENAME).is_file()  # marker 删不掉
    tombstone = evidence_root / "exec-1__node_a" / "upload-delivered.json"
    assert tombstone.is_file()
    record = json.loads(tombstone.read_text(encoding="utf-8"))
    # outcome 随调用点收尾形态记录：204 已交付 / 409 判决丢弃（可区分）。
    assert record["outcome"] == ("delivered" if report_status == 204 else "rejected")

    client2 = QueueFakeClient(report_status=report_status)
    queue2 = _queue(client2)
    try:
        assert queue2.restore(work_root) == 0  # tombstone 命中：不再重投
        queue2.shutdown()
    finally:
        if (work_root / "exec-1").exists():
            os.chmod(work_root / "exec-1", 0o700)

    assert client2.reports == []  # 零重报（修复前每重启一轮重复 report）
    assert not (work_root / "exec-1").exists()  # best-effort 清理（已可写）


def test_restore_requeues_new_lease_marker_despite_old_tombstone(
    tmp_path: Path, evidence_root: Path
) -> None:
    """tombstone 键含 lease_id：旧 lease 的 tombstone 不得压制新 attempt 的
    marker（Host 重排队 → 新 claim 重写 marker）——只有同 (execution_id,
    lease_id) 才跳过。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root)
    marker = work_root / "exec-1" / PENDING_FILENAME
    marker.write_text(json.dumps(task.to_json(), ensure_ascii=False), encoding="utf-8")
    incident = evidence_root / "exec-1__node_a"
    incident.mkdir(parents=True)
    (incident / "upload-delivered.json").write_text(
        json.dumps(
            {
                "version": 1,
                "execution_id": "exec-1",
                "lease_id": "lease-0",  # 旧 lease 的 tombstone
                "node_key": "node_a",
            }
        ),
        encoding="utf-8",
    )
    client = QueueFakeClient()
    queue = _queue(client)

    assert queue.restore(work_root) == 1  # 新 lease（lease-1）照常重投
    queue.shutdown()


@pytest.mark.parametrize("body", ["[]", "null", '"text"'])
def test_malformed_tombstone_payload_is_a_miss_not_a_crash(
    tmp_path: Path, evidence_root: Path, body: str
) -> None:
    """#1184 Finding 2：tombstone 文件是合法 JSON 但非对象（人工清理残留
    / 损坏形态）→ 按「未命中」处理（isinstance 形状校验），restore 照常
    重投该 marker。修复前 ``json.loads`` 合法但 ``payload.get`` 抛
    AttributeError——restore 的 OSError 目录隔离接不住，一个畸形
    tombstone 炸穿 Worker 启动（违背 fail-open 语义）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_marker(work_root)
    incident = evidence_root / "exec-1__node_a"
    incident.mkdir(parents=True)
    (incident / "upload-delivered.json").write_text(body, encoding="utf-8")
    client = QueueFakeClient()
    queue = _queue(client)

    assert queue.restore(work_root) == 1  # 未命中：marker 照常重投
    queue.shutdown()

    assert len(client.reports) == 1


def test_drop_marker_malformed_marker_json_is_removable_orphan(
    tmp_path: Path, evidence_root: Path
) -> None:
    """同族自查（marker 读取面）：marker 是合法 JSON 但非对象 → 与损坏
    JSON 同语义（可清理孤儿，无 lease 归属可判）。修复前 payload.get 抛
    AttributeError 逃出 drop_marker，收尾车道把它当 crashed/aborted。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root)
    (task.execution_dir / PENDING_FILENAME).write_text("[]", encoding="utf-8")
    write_owner_marker(task.execution_dir, {"execution_id": "exec-1", "lease_id": "lease-1"})

    assert drop_marker(task, "delivered") is True

    assert not task.execution_dir.exists()
