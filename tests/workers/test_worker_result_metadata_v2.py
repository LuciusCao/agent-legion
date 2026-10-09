"""#843 PR-2：结果元数据 v2 Worker 写侧（result.json 归档成员）。

钉写侧契约：终态 metadata（含产物清单）写成归档**首成员** result.json
（UTF-8 JSON）；产物清单不再走 v1 换轨成员 result-output-artifacts.json，
payload 绝不携带 output_artifacts_in_archive 标记（PR-1 评审 P3-2 的契约
禁令）；Host 读回面（PR-1 的 result_metadata_reader + 同一 parse 链）对
Worker 产出的归档解析等价。#1098 的心跳空窗用例也在本文件（report 单次
尝试 + 外层 resume→退避→quiesce）。纯单元层（no_db）；Host 路由级
roundtrip 见 tests/routes/test_agent_worker_result_metadata_v2_roundtrip.py。
"""

from __future__ import annotations

import json
import tarfile
import time
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import requests

from server.app.agent_broker.result_metadata_reader import read_archived_result_metadata
from server.app.routes.agent_worker_results import parse_result_metadata
from shared.code_contract import (
    RESULT_METADATA_FORMAT_HEADER,
    RESULT_METADATA_FORMAT_V2,
    RESULT_METADATA_MEMBER,
    RESULT_OUTPUT_ARTIFACTS_FLAG,
    RESULT_OUTPUT_ARTIFACTS_MEMBER,
)
from tests.workers.upload_queue_testlib import (
    QueueFakeClient,
    _execution_dir,
    _queue,
    _task,
)
from worker.host.client import Client
from worker.upload import queue as upload_queue

pytestmark = pytest.mark.no_db

_HASH = "a" * 64


def _archive_metadata_and_members(archive: Path) -> tuple[dict, list[str]]:
    with tarfile.open(archive) as tar:
        members = tar.getnames()
        member = tar.extractfile(RESULT_METADATA_MEMBER)
        assert member is not None, "result archive is missing the result.json member"
        return json.loads(member.read()), members


def _capturing_client(client: QueueFakeClient, captured: dict[str, Any]) -> QueueFakeClient:
    """捕获每次 report 的归档字节（成功后目录即清，report 时刻是唯一快照点）。"""
    original_report = client.report

    def report_and_capture(execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        captured.setdefault("archives", []).append(archive.read_bytes())
        return original_report(execution_id, lease_id, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    return client


# --- v2 写侧契约：result.json 首成员 / 清单内嵌 / 契约禁令 ------------------


def test_delivered_archive_carries_metadata_as_first_member(tmp_path: Path) -> None:
    """bulk 终点 finalize 后的归档：result.json 是首成员（Host 流式扫描即刻
    命中）、载荷即终态 metadata（含 CAS 产物清单）、证据成员照常携带；
    无 v1 换轨成员、无契约禁令标记。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _capturing_client(client, captured)
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["output_artifacts"]["output.json"].startswith("sha256:")
    # 归档成员序：result.json 首位，随后产物与 run_dir 证据成员。
    metadata, members = _archive_metadata_and_members(
        _write_archive(tmp_path, captured["archives"][0])
    )
    assert members[0] == RESULT_METADATA_MEMBER
    assert RESULT_OUTPUT_ARTIFACTS_MEMBER not in members
    assert "output.json" in members
    assert any(name.endswith("events.jsonl") for name in members)
    # 载荷与 fake 读回的上报元数据同源（result.json 即权威）。
    assert metadata == client.reports[0]
    # 契约禁令（PR-1 评审 P3-2）：v2 payload 绝不携带换轨标记。
    assert RESULT_OUTPUT_ARTIFACTS_FLAG not in metadata


def _write_archive(tmp_path: Path, raw: bytes) -> Path:
    archive = tmp_path / "reported.tar.gz"
    archive.write_bytes(raw)
    return archive


def test_direct_upload_manifest_rides_result_json_unchained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """128 条直传 dict ref（~25KB 序列化）——v1 头形态的溢出形态（#748/#755
    降级链的存在理由）；v2 直收：清单整体留在 result.json，一次 report 即
    投递成功（零换轨、零 CAS 重传、无截断标记）。"""
    outputs = tuple(f"output-{i:03d}.json" for i in range(128))
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    job_dir = work_root / "exec-1" / "job"
    for name in outputs:
        (job_dir / name).write_text("{}", encoding="utf-8")

    def direct_upload_ok(path: Path, spec: object, **_kw: object) -> dict:
        return {
            "storage_key": str(dict(spec)["storage_key"]),
            "size_bytes": 2,
            "content_hash": _HASH,
        }

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_ok)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _capturing_client(client, captured)
    task = _task(work_root, expected_outputs=outputs)
    specs = {name: {"storage_key": f"jobs-staging/x/{name}", "url": "http://x"} for name in outputs}
    task.artifact_uploads = dict(specs)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert list(report["output_artifacts"]) == list(outputs)
    serialized = json.dumps(report, ensure_ascii=False).encode("utf-8")
    assert len(serialized) > 16 * 1024  # v1 头预算形态的载荷，v2 无预算直收
    # 产物字节零重传（直传规格不动、CAS 通道零调用）；单趟 report。
    assert client.uploads == {}
    assert task.artifact_uploads == specs
    metadata, members = _archive_metadata_and_members(
        _write_archive(tmp_path, captured["archives"][0])
    )
    assert members[0] == RESULT_METADATA_MEMBER
    assert RESULT_OUTPUT_ARTIFACTS_MEMBER not in members
    assert "output.json" not in members  # 直传：产物字节不在归档
    assert not (work_root / "exec-1").exists()


def test_worker_archive_parses_through_host_v2_reader(tmp_path: Path) -> None:
    """写读等价：Worker 产出的归档经 PR-1 的 Host 读回面（result_metadata_reader
    + 同一 parse 链）解析，outcome/record 与 v1 头路径对同一 metadata 的解析
    逐字段等价——双形态在真实字节上闭环。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _capturing_client(client, captured)
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()

    archive = _write_archive(tmp_path, captured["archives"][0])
    v2_outcome, v2_record = parse_result_metadata(read_archived_result_metadata(archive))
    expected_metadata = client.reports[0]
    v1_outcome, v1_record = parse_result_metadata(json.dumps(expected_metadata, ensure_ascii=False))
    assert v2_outcome == v1_outcome
    assert v2_record == v1_record
    assert v2_outcome.status == "completed"
    assert v2_outcome.output_artifacts == expected_metadata["output_artifacts"]


def test_code_lane_archive_carries_metadata_first(tmp_path: Path) -> None:
    """code 车道同契约：result.json 首成员 + node.log 照常携带（prepare_
    code_result 的键集契约不受 v2 影响）。"""
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    (execution_dir / "node.log").write_text("stdout", encoding="utf-8")
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _capturing_client(client, captured)
    task = _task(
        work_root,
        exec_kind="code",
        code_result={"status": "completed", "error_message": ""},
    )
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    metadata, members = _archive_metadata_and_members(
        _write_archive(tmp_path, captured["archives"][0])
    )
    assert members[0] == RESULT_METADATA_MEMBER
    assert "node.log" in members
    assert metadata["status"] == "completed"
    assert set(metadata) == {
        "status",
        "exit_code",
        "error_message",
        "command",
        "output_artifacts",
    }


def test_prebuilt_task_reports_metadata_only_archive(tmp_path: Path) -> None:
    """prebuilt 形态：无产物无证据，归档仅含 result.json（判败/预构建路径的
    合法最小形态）。"""
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _capturing_client(client, captured)
    task = _task(
        work_root,
        kind="prebuilt",
        prebuilt_metadata={"status": "failed", "exit_code": 1, "error_message": "x"},
    )
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    metadata, members = _archive_metadata_and_members(
        _write_archive(tmp_path, captured["archives"][0])
    )
    assert members == [RESULT_METADATA_MEMBER]
    assert metadata["status"] == "failed"
    assert not (work_root / "exec-1").exists()


# --- #1098：report 单次尝试的心跳空窗（验收：持续超时下空窗 < lease TTL） ---


class _StallingReportClient(Client):
    """路径分派桩：/result 每次先停摆 stall 秒再模拟传输超时（前 fail 次），
    之后 204；heartbeat 记录拍点时间——量最大拍间空窗。"""

    def __init__(self, stall: float, fail: int) -> None:
        super().__init__("http://unused")
        self.stall = stall
        self.fail = fail
        self.result_calls = 0
        self.beat_times: list[float] = []

    def request(self, method, path, *, data=None, headers=None, timeout=None, stream_to=None):  # type: ignore[no-untyped-def]
        if path.endswith("/heartbeat"):
            self.beat_times.append(time.monotonic())
            return 204, []
        assert path.endswith("/result"), path
        self.result_calls += 1
        if self.result_calls <= self.fail:
            time.sleep(self.stall)  # 在途停摆（模拟挂起的连接/超时窗口）
            raise requests.ConnectionError("timed out")
        return 204, b""


def test_report_persistent_timeout_keeps_heartbeat_gap_under_lease_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1098 验收：/result 持续超时（每次尝试停满超时窗口）、heartbeat 正常
    时，两次 report 尝试之间的心跳空窗 < lease TTL（90s）。

    尺度映射：真实 transfer_timeout=120s、lease TTL=90s；本用例按
    stall=0.5s / interval=0.05s 等比缩放——修复前（内层 3 次连打）单次外层
    尝试即产生 3×stall + 内层退避的静默窗（真实尺度 ≈360s > 90s，租约被
    过期清扫、重报 409 丢结果）；修复后空窗 ≤ 单次尝试时长（真实尺度
    ≤120s——单次在途停摆是「report 即最后存活证明」语义下的固有残差，
    由 409/ownership_lost 终态收口）。"""
    monkeypatch.setattr(upload_queue, "_RETRY_BASE_SECONDS", 0.2)
    stall = 0.5
    work_root = tmp_path / "work"
    (work_root / "exec-1").mkdir(parents=True)
    client = _StallingReportClient(stall=stall, fail=2)
    queue = _queue(client)  # legacy 单拍模式：真实心跳线程（0.05s 一拍）
    task = _task(
        work_root,
        kind="prebuilt",
        prebuilt_metadata={"status": "completed", "exit_code": 0, "output_artifacts": {}},
    )
    queue.submit(task)
    queue.shutdown()

    assert client.result_calls == 3  # 外层恰 3 次尝试（2 失败 + 1 成功）——每次单发
    # 退避窗（0.2s ≫ 0.05s 一拍）内心跳确实在跳：两窗各 ≥ 数拍。
    assert len(client.beat_times) >= 4
    gaps = [b - a for a, b in pairwise(client.beat_times)]
    max_gap = max(gaps)
    # 空窗上界：单次尝试停摆 + 一拍 interval + 调度余量（修复前 ≥ 3×stall）。
    assert max_gap < 2 * stall, f"heartbeat stalled {max_gap:.3f}s across report attempts"
    # issue 验收原文（真实尺度 < 90s）的等比断言：空窗远小于缩放后的 TTL。
    assert max_gap < 90
    assert not (work_root / "exec-1").exists()


def test_report_headers_use_shared_constants() -> None:
    """头名/值取 shared/code_contract 单一事实来源（写侧与 Host 读侧同源，
    字面量漂移即断）。"""
    assert RESULT_METADATA_FORMAT_HEADER == "X-Agent-Result-Format"
    assert RESULT_METADATA_FORMAT_V2 == "2"
    assert RESULT_METADATA_MEMBER == "result.json"


# --- 换轨降级臂同步换写 result.json（report_policy 的 v2 归档同步） --------


def test_degrade_rewrites_result_json_and_keeps_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4xx 判决降级：证据成员保留、result.json 换写为判败载荷——重报的归档
    即新载荷（Host 读回取首个命中，旧成员不得残留）。"""
    monkeypatch.setattr(upload_queue, "_RETRY_BASE_SECONDS", 0.001)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient(report_status=400)
    captured: dict[str, Any] = {}
    original_report = _capturing_client(client, captured).report

    def scripted(execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        # 首趟 400 判决 → 降级重报 204（投递成功）。
        client.report_status = 204 if captured.get("archives") else 400
        return original_report(execution_id, lease_id, archive)

    client.report = scripted  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()

    assert len(captured["archives"]) == 2  # 原报 + 判败重报，恰两趟
    # 重报归档：result.json 仍在首位且唯一（旧成员被换写替换），证据保留。
    metadata, members = _archive_metadata_and_members(
        _write_archive(tmp_path, captured["archives"][1])
    )
    assert members.count(RESULT_METADATA_MEMBER) == 1
    assert members[0] == RESULT_METADATA_MEMBER
    assert any(name.endswith("events.jsonl") for name in members)
    assert metadata["status"] == "failed"
    assert "HTTP 400" in metadata["error_message"]
    assert not (work_root / "exec-1").exists()
