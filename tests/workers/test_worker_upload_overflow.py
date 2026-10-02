"""结果头预算溢出（直传 ref 清单归档回退）与 DirectUploadError 换轨预检一族用例
（自 test_worker_upload_queue.py 拆出，同一条 800 行拆分线）。

钉 #748 R3 / #755 codex P1 的清单归档协议（ResultHeaderOverflow → embed
result-output-artifacts.json → 重报）与 embed_precheck 换轨判败通道；
共享桩/工具见 tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import threading
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
from worker.upload.queue import UploadTask

# -- #748 R3 / #755 codex P1：结果头预算溢出（直传 ref 形态）的清单归档回退 --


def _overflow_then_capture(client: QueueFakeClient, captured: dict[str, Any]) -> None:
    """模拟真实 Client 的头序列化：直传 dict ref 形态抛溢出信号；重报（清单
    已清空 + in_archive 标记）不再抛——与 worker.host.transfer 的信号纪律
    一致。顺带捕获第二趟 report 的 metadata、归档成员清单与清单成员内容。"""
    import tarfile

    from worker.host.transfer import ResultHeaderOverflow

    original_report = client.report

    def report_with_overflow(execution_id, lease_id, metadata, archive):
        captured["attempts"] = captured.get("attempts", 0) + 1
        if any(isinstance(ref, dict) for ref in metadata.get("output_artifacts", {}).values()):
            raise ResultHeaderOverflow("result header over budget with direct-upload refs")
        captured["metadata"] = dict(metadata)
        with tarfile.open(archive) as tar:
            captured["members"] = tar.getnames()
            # embed 失败路径归档保持原形态（无清单成员）：按名字探测而非
            # extractfile 直接取（缺成员抛 KeyError）。
            member = (
                tar.extractfile("result-output-artifacts.json")
                if "result-output-artifacts.json" in captured["members"]
                else None
            )
            captured["manifest"] = json.loads(member.read()) if member is not None else None
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_with_overflow  # type: ignore[method-assign]


def test_result_header_overflow_embeds_manifest_in_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#755 codex P1 队列级复现：128 个产物的直传任务，直传 ref 清单 ~25KB
    撞破 14KB 头预算。新协议：产物字节不动（已在 S3，零 CAS 重传），完整
    direct-ref 清单写成归档首成员 result-output-artifacts.json，头里只带
    output_artifacts_in_archive 标记，重报即投递成功。"""
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
            "content_hash": "a" * 64,
        }

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_ok)
    client = QueueFakeClient()
    task = _task(work_root, expected_outputs=outputs)
    specs = {name: {"storage_key": f"jobs-staging/x/{name}", "url": "http://x"} for name in outputs}
    task.artifact_uploads = dict(specs)
    captured: dict[str, Any] = {}
    _overflow_then_capture(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2  # 溢出 → embed → 重报，恰两趟
    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    # 头里只剩标记：清单清空、无截断标记（截断是 CAS 形态的最后手段）。
    assert report["output_artifacts"] == {}
    assert report["output_artifacts_in_archive"] is True
    assert "output_artifacts_truncated" not in report
    # 归档首成员即完整 direct-ref 清单（Host 流式扫描几 KB 即命中）。
    assert captured["members"][0] == "result-output-artifacts.json"
    assert captured["manifest"] == {
        name: {"storage_key": f"jobs-staging/x/{name}", "size_bytes": 2, "content_hash": "a" * 64}
        for name in outputs
    }
    # 产物字节零重传：CAS 通道从未被调用；直传规格保持不动。
    assert client.uploads == {}
    assert task.artifact_uploads == specs
    # 重报 204：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()


def test_result_header_overflow_embed_failure_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """embed 失败（OSError/tarfile/契约违例）→ 诚实判败：failed_metadata
    上报（清单不可交付即产物引用不可用），归档保持原形态，不重试不死循环。"""
    from worker.upload import report as report_module

    def failing_embed(archive, artifacts, expected_outputs, max_bytes=0) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(report_module, "embed_output_artifacts_manifest", failing_embed)
    work_root = tmp_path / "work"
    _execution_dir(work_root)

    def direct_upload_ok(path: Path, spec: object, **_kw: object) -> dict:
        return {
            "storage_key": "jobs-staging/x/output.json",
            "size_bytes": 2,
            "content_hash": "a" * 64,
        }

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_ok)
    client = QueueFakeClient()
    task = _task(work_root)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    captured: dict[str, Any] = {}
    _overflow_then_capture(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "manifest embed failed" in report["error_message"]
    assert report["output_artifacts"] == {}
    assert client.uploads == {}  # 零 CAS 重传
    # 判败上报成功（204）：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()


# -- #755 codex P1：DirectUploadError 换轨预检（上限来自 claim 下发） --


def _direct_upload_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """直传通道终态失败（DirectUploadError）→ 触发队列的换轨判定。"""
    from worker.artifact.upload import DirectUploadError

    def direct_upload_fails(path: Path, spec: object, **_kw: object) -> dict:
        raise DirectUploadError("HTTP 403")

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_fails)


def test_direct_upload_fallback_oversize_embed_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换轨预检：内嵌总量（产物 + run_dir 实测）超「claim 下发的
    max_archive_bytes − 安全余量」时**不换轨**——重内嵌会把大产出人群送进
    Host 413 → 丢结果 → 租约过期全量重跑（每轮同样 413）。本地诚实判败：
    failed_metadata 上报，归档保持直传形态（产物字节本就不在 tar，events/
    日志照常携带），直传规格保留、CAS 通道零上传。"""
    from worker.upload import embed_precheck

    # 用小常量代替 1 MiB 余量，避免 tmp 盘写大文件（总量口径与上限同源断言）。
    monkeypatch.setattr(embed_precheck, "EMBED_SAFETY_MARGIN_BYTES", 0)
    outputs = tuple(f"output-{i:03d}.json" for i in range(3))
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    job_dir = work_root / "exec-1" / "job"
    for name in outputs:
        (job_dir / name).write_bytes(b"\0" * 1024)  # 3 KiB > 1 KiB 下发上限
    run_dir_bytes = sum(
        p.stat().st_size for p in (job_dir / "runs" / "node_a" / "worker").rglob("*") if p.is_file()
    )

    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root, expected_outputs=outputs, max_archive_bytes=1024)
    task.artifact_uploads = {
        name: {"storage_key": f"jobs-staging/x/{name}", "url": "http://x"} for name in outputs
    }
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "archive-embed ceiling" in report["error_message"]
    # 未压缩口径的总量如实上报：产物 + run_dir 实测。
    assert f"totals {3072 + run_dir_bytes} bytes" in report["error_message"]
    assert report["output_artifacts"] == {}
    # 未换轨：直传规格保留、CAS 通道零上传。
    assert task.artifact_uploads
    assert client.uploads == {}
    # 判败上报成功（204）：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()


def test_direct_upload_fallback_within_ceiling_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内嵌总量 ≤ 下发上限 − 余量时正常换轨：清规格重跑 prepare（tar 内嵌
    产物）、CAS 通道上传全部产物、CAS 形态清单上报完成。"""
    _direct_upload_fails(monkeypatch)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    task = _task(work_root, max_archive_bytes=64 * 1024 * 1024)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    # 换轨终点：CAS 形态清单 + 产物字节确实重传。
    assert report["output_artifacts"]["output.json"].startswith("sha256:")
    assert len(client.uploads) == 1
    assert not task.artifact_uploads  # 规格已清
    assert not (work_root / "exec-1").exists()


def test_direct_upload_fallback_default_ceiling_without_claim_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧 Host 未下发（max_archive_bytes=0）→ 预检回落 64 MiB 默认：小产物
    照常换轨，与无规格任务同一语义。"""
    _direct_upload_fails(monkeypatch)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    task = _task(work_root)  # max_archive_bytes 默认 0
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert client.reports[0]["status"] == "completed"
    assert len(client.uploads) == 1


def test_direct_upload_fallback_missing_output_does_not_block_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#755 对抗复审 P3：缺席的 expected output 按 0 字节计（不内嵌任何字节，
    Host 侧 Missing outputs 判定不受影响），预检不得把它当「大小未知」拒绝
    换轨——否则会拿体积措辞误导排障。"""
    outputs = ("output.json", "gone.json")
    work_root = tmp_path / "work"
    _execution_dir(work_root)  # 只造 output.json；gone.json 缺席按 0 字节计

    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root, expected_outputs=outputs)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["output_artifacts"]["output.json"].startswith("sha256:")
    assert "could not be stat'ed" not in report["error_message"]


def test_direct_upload_fallback_unstattable_output_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预检的 stat 失败臂：文件存在但 stat 抛 OSError（权限/IO 错误）→ 大小
    未知即不可证安全，拒绝换轨、本地诚实判败。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)

    # 投弹窗口必须收窄到预检内部：全局 patch Path.stat 会波及 prepare 的
    # is_file()（裸 OSError 无 errno，不在 pathlib 可忽略族而重抛），初次
    # prepare 就降级成 "result preparation failed"，永远走不到换轨判定。
    # 用 threading.local 武装窗口（队列跑在调度池线程），炸弹只落在
    # embed_precheck 对 expected output 的 stat 上。
    bomb = threading.local()
    real_stat = Path.stat

    def flaky_stat(self: Path, *args: object, **kwargs: object) -> Any:
        if getattr(bomb, "armed", False) and self.name == "output.json":
            raise OSError("permission denied")
        return real_stat(self, *args, **kwargs)

    real_rejection = upload_queue.embed_switch_rejection

    def armed_rejection(task: UploadTask) -> str | None:
        bomb.armed = True
        try:
            return real_rejection(task)
        finally:
            bomb.armed = False

    monkeypatch.setattr(Path, "stat", flaky_stat)
    monkeypatch.setattr(upload_queue, "embed_switch_rejection", armed_rejection)
    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "could not be stat'ed" in report["error_message"]
    assert "archive-embed ceiling" in report["error_message"]
    assert client.uploads == {}  # 未换轨
    assert not (work_root / "exec-1").exists()


def test_direct_upload_fallback_code_lane_node_log_counted_in_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """code 车道的 node.log 写在 execution_dir 根（不在 run_dir），是沙箱
    stdout/stderr 的无上限捕获，可以是内嵌 tar 的最大成员。预检不计入它，
    「大且不可压缩的 node.log + 小产物」会被放进换轨，重备 tar 超 Host
    上限 → 413 → 丢结果 → 全量重跑死循环。"""
    from worker.upload import embed_precheck

    monkeypatch.setattr(embed_precheck, "EMBED_SAFETY_MARGIN_BYTES", 0)
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    # 产物只有 2 字节（output.json "{}"）；node.log 一个就超预检上限。
    (execution_dir / "node.log").write_bytes(b"\0" * 2048)
    run_dir_bytes = sum(
        p.stat().st_size
        for p in (execution_dir / "job" / "runs" / "node_a" / "worker").rglob("*")
        if p.is_file()
    )

    _direct_upload_fails(monkeypatch)
    client = QueueFakeClient()
    task = _task(work_root, max_archive_bytes=1024)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "archive-embed ceiling" in report["error_message"]
    # 总量口径含 node.log：产物 2B + run_dir 实测 + node.log 2048B。
    assert f"totals {2 + run_dir_bytes + 2048} bytes" in report["error_message"]
    assert task.artifact_uploads  # 未换轨
    assert client.uploads == {}
    assert not (work_root / "exec-1").exists()


# -- #755 codex R8 P2：embed 后归档上限重校（替换前拒写 + re-stat 兜底） --


def _overflow_capture_with_size(client: QueueFakeClient, captured: dict[str, Any]) -> None:
    """同 _overflow_then_capture 的信号纪律（dict refs 抛溢出、重报不抛），
    记录 metadata 与重报归档的实测大小/字节（padding 假 embed 的归档不是
    合法 tar，按需开不开箱由用例决定）。"""
    from worker.host.transfer import ResultHeaderOverflow

    original_report = client.report

    def report_with_overflow(execution_id, lease_id, metadata, archive):
        captured["attempts"] = captured.get("attempts", 0) + 1
        if any(isinstance(ref, dict) for ref in metadata.get("output_artifacts", {}).values()):
            raise ResultHeaderOverflow("result header over budget with direct-upload refs")
        captured["metadata"] = dict(metadata)
        captured["archive_size"] = archive.stat().st_size
        captured["archive_bytes"] = archive.read_bytes()
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_with_overflow  # type: ignore[method-assign]


def _direct_upload_task(
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    max_archive_bytes: int,
    outputs: tuple[str, ...] = ("output.json",),
) -> UploadTask:
    def direct_upload_ok(path: Path, spec: object, **_kw: object) -> dict:
        return {
            "storage_key": str(dict(spec)["storage_key"]),
            "size_bytes": 2,
            "content_hash": "a" * 64,
        }

    monkeypatch.setattr(upload_queue, "upload_artifact_direct", direct_upload_ok)
    task = _task(work_root, expected_outputs=outputs, max_archive_bytes=max_archive_bytes)
    task.artifact_uploads = {
        name: {"storage_key": f"jobs-staging/x/{name}", "url": "http://x"} for name in outputs
    }
    return task


def _padding_embed(monkeypatch: pytest.MonkeyPatch, target_size: int) -> None:
    """假 embed：产出恰好 target_size 字节的「归档」——绕过 embed 内部的
    上限预检（那条由真实 embed 的用例覆盖），专测 report 侧 re-stat 兜底
    的边界判定。"""
    from worker.upload import report as report_module

    def embed(archive: Path, artifacts: dict, expected_outputs: tuple, max_bytes: int = 0) -> None:
        archive.write_bytes(b"\0" * target_size)

    monkeypatch.setattr(report_module, "embed_output_artifacts_manifest", embed)


def test_result_header_overflow_embed_over_ceiling_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#755 codex R8 P2 复现：原直传归档低于但接近 claim 下发的
    max_archive_bytes，embed 新增清单成员把它推过 Host 大小门禁。修复前
    重报必撞 413，而 report 循环把非 204 当终态删 marker——结果无法提交、
    staging 产物永不登记。修复后 embed 在原子替换前按 staging 实际大小拒写
    （原归档未动、证据保全），走与换轨预检判败相同的诚实失败通道。"""
    outputs = tuple(f"output-{i:03d}.json" for i in range(128))
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    job_dir = execution_dir / "job"
    for name in outputs:
        (job_dir / name).write_text("{}", encoding="utf-8")
    ceiling = 48 * 1024
    # 不可压缩噪音把原归档推到「低于但接近」下发上限；随机 hex 的清单 ref
    # （弱可压缩）把 embed 后的归档推过上限。
    run_dir = job_dir / "runs" / "node_a" / "worker"
    (run_dir / "noise.bin").write_bytes(os.urandom(44 * 1024))
    specs = {
        name: {"storage_key": f"jobs-staging/x/{os.urandom(32).hex()}", "url": "http://x"}
        for name in outputs
    }
    task = _direct_upload_task(work_root, monkeypatch, ceiling, outputs)
    task.artifact_uploads = dict(specs)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}

    from worker.host.transfer import ResultHeaderOverflow

    original_report = client.report

    def report_with_overflow(execution_id, lease_id, metadata, archive):
        captured["attempts"] = captured.get("attempts", 0) + 1
        if any(isinstance(ref, dict) for ref in metadata.get("output_artifacts", {}).values()):
            raise ResultHeaderOverflow("result header over budget with direct-upload refs")
        captured["metadata"] = dict(metadata)
        captured["archive_size"] = archive.stat().st_size
        with tarfile.open(archive) as tar:
            captured["members"] = tar.getnames()
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_with_overflow  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2  # 溢出 → embed 拒写 → 诚实判败重报
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "Host archive ceiling" in report["error_message"]
    assert report["output_artifacts"] == {}
    # 重报的是原归档（可提交体积、证据保全）：无清单成员、events 仍在。
    assert captured["archive_size"] <= ceiling
    assert "result-output-artifacts.json" not in captured["members"]
    assert any(member.endswith("events.jsonl") for member in captured["members"])
    # 直传规格保留、CAS 通道零上传。
    assert task.artifact_uploads == specs
    assert client.uploads == {}
    # 判败上报成功（204）：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()


def test_overflow_restast_allows_archive_exactly_at_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """off-by-one 边界：embed 后恰好等于上限可提交（Host 门禁是严格大于才
    413）——重报照常推进、不判败。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    ceiling = 4096
    _padding_embed(monkeypatch, ceiling)
    task = _direct_upload_task(work_root, monkeypatch, ceiling)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _overflow_capture_with_size(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["output_artifacts_in_archive"] is True
    assert captured["archive_size"] == ceiling
    assert not (work_root / "exec-1").exists()


def test_overflow_restast_one_byte_over_ceiling_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """off-by-one 边界：超上限 1 字节——embed 内部预检被假 embed 绕过时，
    report 侧 re-stat 兜底回收空归档诚实判败：重报的归档必须 ≤ 上限且仍
    是合法（空）tar，不重报大归档吃 413。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    ceiling = 4096
    _padding_embed(monkeypatch, ceiling + 1)
    task = _direct_upload_task(work_root, monkeypatch, ceiling)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _overflow_capture_with_size(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "grew the result archive past" in report["error_message"]
    assert report["output_artifacts"] == {}
    # 回收后的空归档：可提交体积，且是合法 tar（Host 侧解得开）。
    assert captured["archive_size"] <= ceiling
    with tarfile.open(fileobj=io.BytesIO(captured["archive_bytes"])) as tar:
        assert tar.getnames() == []
    assert not (work_root / "exec-1").exists()


def test_overflow_restast_default_ceiling_without_claim_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """边界：旧 Host 未下发（max_archive_bytes=0）→ re-stat 回落 64 MiB
    默认——4097 字节的 embed 归档照常提交（误把 0 当上限会误判败）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _padding_embed(monkeypatch, 4097)
    task = _direct_upload_task(work_root, monkeypatch, 0)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _overflow_capture_with_size(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2
    assert client.reports[0]["status"] == "completed"
    assert not (work_root / "exec-1").exists()


def test_overflow_ceiling_rejection_empties_oversized_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """对抗复审边界：头溢出在任何大小检查之前抛出，原归档本身可能已超
    上限却从未过 Host 大小门禁。embed 拒写后的诚实判败若重报这份超限
    原归档，只会吃 413 被当终态删 marker——判败通道先把归档回收成可
    提交体积（空归档 + failed_metadata）。"""
    from worker.upload import report as report_module
    from worker.upload.result_manifest import ManifestEmbedExceedsArchiveCeiling

    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    ceiling = 1024
    # 原归档（run_dir 噪音）本身就超下发上限。
    (execution_dir / "job" / "runs" / "node_a" / "worker" / "noise.bin").write_bytes(
        os.urandom(2048)
    )

    def rejecting_embed(
        archive: Path, artifacts: dict, expected_outputs: tuple, max_bytes: int = 0
    ) -> None:
        raise ManifestEmbedExceedsArchiveCeiling("over the ceiling; original untouched")

    monkeypatch.setattr(report_module, "embed_output_artifacts_manifest", rejecting_embed)
    task = _direct_upload_task(work_root, monkeypatch, ceiling)
    client = QueueFakeClient()
    captured: dict[str, Any] = {}
    _overflow_capture_with_size(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "over the ceiling" in report["error_message"]
    # 回收后的空归档：可提交体积、合法 tar。
    assert captured["archive_size"] <= ceiling
    with tarfile.open(fileobj=io.BytesIO(captured["archive_bytes"])) as tar:
        assert tar.getnames() == []
    assert not (work_root / "exec-1").exists()


# -- #755 codex P2-1：余量是上限的函数（小上限合法配置下预检不可误杀） --


def test_embed_safety_margin_scales_with_ceiling() -> None:
    """余量按 min(固定余量, 上限/4) 收缩：默认 64 MiB 上限行为不变；任何
    合法上限（实例设置 gt=0 即合法，含上限 < 固定余量的小配置）下预算
    恒为正——修复前 ceiling - 1 MiB 在小上限下为负，几十字节的产物也
    必被预检拒绝。"""
    from worker.upload.embed_precheck import EMBED_SAFETY_MARGIN_BYTES, embed_safety_margin

    assert embed_safety_margin(64 * 1024 * 1024) == EMBED_SAFETY_MARGIN_BYTES
    assert embed_safety_margin(1024) == 256
    for ceiling in (1, 2, 100, 1024, EMBED_SAFETY_MARGIN_BYTES):
        assert ceiling - embed_safety_margin(ceiling) > 0


def test_direct_upload_fallback_sub_margin_ceiling_still_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限 < 固定余量（合法小配置）+ 微小产物 + presigned 暂时失败 → 预检
    放行、归档/CAS 回退成功。修复前 ceiling - EMBED_SAFETY_MARGIN_BYTES 为
    负，完全装得进 Host 上限的结果被直接上报 failed 而不是回退换轨。"""
    _direct_upload_fails(monkeypatch)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    task = _task(work_root, max_archive_bytes=8 * 1024)  # 8 KiB << 1 MiB 固定余量
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "completed"
    assert report["output_artifacts"]["output.json"].startswith("sha256:")
    assert len(client.uploads) == 1  # CAS 回退通道确实重传
    assert not task.artifact_uploads
    assert not (work_root / "exec-1").exists()


def test_embed_switch_rejection_ceiling_equal_to_margin(tmp_path: Path) -> None:
    """上限 == 固定余量边界：有效余量收缩为上限/4，预算 = 3/4 上限 > 0，
    微小产物放行（修复前预算恰好为 0，任何非零产物都被拒绝换轨）。"""
    from worker.upload.embed_precheck import EMBED_SAFETY_MARGIN_BYTES, embed_switch_rejection

    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root, max_archive_bytes=EMBED_SAFETY_MARGIN_BYTES)
    assert embed_switch_rejection(task) is None


def test_direct_upload_fallback_restast_backstop_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换轨后的 re-stat 兜底：余量按上限比例收缩后，预检（未压缩口径）可能
    放行「实际归档仍超 Host 上限」的形态（小上限下 tar/gzip 开销占比不可
    忽略）——重报大归档只会吃 413 被 report 循环当终态删 marker。与
    report.py 的 embed 超限臂同形：回收成空归档诚实判败。"""
    # 强制预检放行（预检的放行/判败面由上一条族覆盖），专测换轨后兜底。
    monkeypatch.setattr(upload_queue, "embed_switch_rejection", lambda task: None)
    _direct_upload_fails(monkeypatch)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    ceiling = 64  # 任何合法 tar.gz 归档都超此上限
    task = _task(work_root, max_archive_bytes=ceiling)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    captured: dict[str, Any] = {}
    original_report = client.report

    def report_and_capture(execution_id, lease_id, metadata, archive):
        captured["archive_size"] = archive.stat().st_size
        captured["archive_bytes"] = archive.read_bytes()
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "archive-embedded fallback" in report["error_message"]
    assert report["output_artifacts"] == {}
    # 回收后的空归档：可提交体积、合法 tar；CAS 通道零上传。
    assert captured["archive_size"] <= ceiling
    with tarfile.open(fileobj=io.BytesIO(captured["archive_bytes"])) as tar:
        assert tar.getnames() == []
    assert client.uploads == {}
    assert not (work_root / "exec-1").exists()


# -- #755 codex R10 P2：embed 重写窗口（心跳已暂停、最终请求未发出）的心跳保持 --


def test_result_header_overflow_embed_window_keeps_heartbeat_beating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#755 codex R10 P2：embed 重写（完整解压 + 重新 gzip）在大归档/慢
    存储上可超过 Host 租约 TTL，而 report_task 进场即 quiesce 心跳、最终
    请求尚未发出——真空窗口内租约被过期清扫，重写结束后的重报吃 409、
    删 marker、整次执行重跑。修复后 embed 窗口内心跳重新武装（计数断言
    重写期间确实在跳），重写完成 quiesce 后重报 204 交付。"""
    import time

    from worker.upload import report as report_module
    from worker.upload.result_manifest import embed_output_artifacts_manifest as real_embed

    client = QueueFakeClient()
    beats_in_embed: list[int] = []

    def slow_embed(archive, artifacts, expected_outputs, max_bytes=0):
        before = client.heartbeats
        time.sleep(0.3)  # 模拟慢存储重写窗口；测试队列 heartbeat_interval=0.05
        beats_in_embed.append(client.heartbeats - before)
        real_embed(archive, artifacts, expected_outputs, max_bytes=max_bytes)

    monkeypatch.setattr(report_module, "embed_output_artifacts_manifest", slow_embed)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _direct_upload_task(work_root, monkeypatch, 64 * 1024 * 1024)
    captured: dict[str, Any] = {}
    _overflow_then_capture(client, captured)
    queue = _queue(client)  # legacy 单拍模式：心跳线程真实跳动（0.05s 一拍）
    queue.submit(task)
    queue.shutdown()

    assert captured["attempts"] == 2  # 溢出 → embed → 重报，恰两趟
    # 重写窗口（0.3s ≫ 0.05s 一拍）内心跳确实在跳——修复前该窗口心跳停摆
    # （计数恒为 0），租约对 Host 无任何存活证明，超 TTL 即被过期清扫。
    assert beats_in_embed and beats_in_embed[0] >= 2
    # 重写后重报 204 交付（不拿 409、不判败）：completed + in_archive 标记臂。
    assert client.reports[0]["status"] == "completed"
    assert client.reports[0]["output_artifacts_in_archive"] is True
    assert not (work_root / "exec-1").exists()
