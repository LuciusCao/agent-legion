"""直传 → 归档内嵌换轨的体积预检与 v2 finalize 大小门一族用例
（自 test_worker_upload_queue.py 拆出，同一条 800 行拆分线）。

#843 PR-2 起结果头降级链（ResultHeaderOverflow → embed
result-output-artifacts.json → 重报）整体退役：产物清单留在 result.json 里，
本文件只钉 DirectUploadError 的换轨预检（embed_precheck）与 bulk 车道终点
finalize 的大小门（超限诚实判败）；共享桩/工具见
tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import tarfile
import threading
from pathlib import Path
from typing import Any

import pytest

from shared.code_contract import RESULT_METADATA_MEMBER
from tests.workers.upload_queue_testlib import (
    QueueFakeClient,
    _execution_dir,
    _queue,
    _task,
)
from worker.upload import queue as upload_queue
from worker.upload.queue import UploadTask

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


# -- #755 codex P2-1：余量是上限的函数（小上限合法配置下预检不可误杀） --


def test_embed_safety_margin_scales_with_ceiling() -> None:
    """余量按 min(固定余量, 上限/4) 收缩：默认 64 MiB 上限行为不变；任何
    合法上限（实例设置 ge=1 KiB 即合法，含上限 < 固定余量的小配置）下预算
    恒为正——修复前 ceiling - 1 MiB 在小上限下为负，几十字节的产物也
    必被预检拒绝。"""
    from worker.upload.embed_precheck import EMBED_SAFETY_MARGIN_BYTES, embed_safety_margin

    assert embed_safety_margin(64 * 1024 * 1024) == EMBED_SAFETY_MARGIN_BYTES
    assert embed_safety_margin(1024) == 256
    for ceiling in (1024, 2048, 100_000, EMBED_SAFETY_MARGIN_BYTES):
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


# -- #843 PR-2：bulk 终点 finalize 的大小门（原 re-stat 兜底面的收口） --


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


def _capture_archive(client: QueueFakeClient, captured: dict[str, Any]) -> None:
    """捕获每次 report 的 metadata 与重报归档的实测大小/成员。"""
    original_report = client.report

    def report_and_capture(execution_id: str, lease_id: str, archive: Path) -> tuple[int, bytes]:
        captured["attempts"] = captured.get("attempts", 0) + 1
        captured["archive_size"] = archive.stat().st_size
        captured["archive_bytes"] = archive.read_bytes()
        with tarfile.open(archive) as tar:
            captured["members"] = tar.getnames()
        return original_report(execution_id, lease_id, archive)

    client.report = report_and_capture  # type: ignore[method-assign]


def test_finalize_over_ceiling_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#755 codex P2-1 收口（原 re-stat 兜底）：预检是未压缩口径的优化放行，
    换轨重备的最终归档（含 result.json 成员）仍可能超 Host 上限——bulk 终点
    finalize 按 staging 实际大小拒写（原归档不动、证据由判败回收取代），
    走与换轨预检相同的诚实失败通道：可提交的 metadata-only 归档重报，
    不重报大归档吃 413 后被当终态删 marker。"""
    import os

    from worker.upload import prepare as upload_prepare

    # 强制两道预检放行（各自的放行/判败面由上方用例族覆盖），专测 finalize
    # 门：#959 起 prepare 预检会先拦下超限 body 归档，本用例构造的是「预检
    # 放行、最终归档超限」的余量带内形态。
    monkeypatch.setattr(upload_queue, "embed_switch_rejection", lambda task: None)
    monkeypatch.setattr(upload_prepare, "declared_ceiling_rejection", lambda task, archive: None)
    _direct_upload_fails(monkeypatch)
    work_root = tmp_path / "work"
    execution_dir = _execution_dir(work_root)
    # 不可压缩产物：换轨重备后 body 归档（内嵌产物）+ result.json 超过上限。
    (execution_dir / "job" / "output.json").write_bytes(os.urandom(4096))
    client = QueueFakeClient()
    ceiling = 1024
    task = _task(work_root, max_archive_bytes=ceiling)
    task.artifact_uploads = {"output.json": {"storage_key": "jobs-staging/x", "url": "http://x"}}
    captured: dict[str, Any] = {}
    _capture_archive(client, captured)
    queue = _queue(client)
    queue.submit(task)
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "result metadata finalize failed" in report["error_message"]
    assert report["output_artifacts"] == {}
    # 回收后的 metadata-only 归档：可提交体积（≤ 上限）、合法 tar、载荷即判败。
    assert captured["members"] == [RESULT_METADATA_MEMBER]
    assert _read_result_metadata_from_bytes(captured["archive_bytes"])["status"] == "failed"
    assert captured["archive_size"] <= ceiling
    # 换轨预检被强制放行（专测 finalize 门）：CAS 上传已发生（1 条），finalize
    # 拒写后不再有任何传输——旧 re-stat 兜底在重备后、上传前拦截；新门在
    # bulk 终点（产物清单终态后），口径是最终归档的实际大小。
    assert len(client.uploads) == 1
    assert not (work_root / "exec-1").exists()


def _read_result_metadata_from_bytes(raw: bytes) -> dict:
    import io
    import json

    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        member = tar.extractfile(RESULT_METADATA_MEMBER)
        assert member is not None
        return json.loads(member.read())


def test_finalize_allows_archive_exactly_at_ceiling(tmp_path: Path) -> None:
    """off-by-one 边界：最终归档恰好等于上限可提交（Host 门禁与 finalize
    拒写条件都是严格大于才拒）——恰等于时照常写入投递。"""
    import shutil

    from worker.upload.prepare import prepare_result
    from worker.upload.result_manifest import (
        embed_result_metadata,
        finalize_result_metadata,
    )

    work_root = tmp_path / "work"
    _execution_dir(work_root)
    task = _task(work_root, max_archive_bytes=64 * 1024 * 1024)
    metadata, body, _outputs = prepare_result(task)
    # 探针：同一 body（字节级副本）embed 一次量出最终尺寸，作恰等上限。
    probe = body.with_name("probe.tar.gz")
    shutil.copy(body, probe)
    embed_result_metadata(probe, metadata)
    ceiling = probe.stat().st_size
    probe.unlink()

    task.max_archive_bytes = ceiling
    final_metadata, final_archive = finalize_result_metadata(task, metadata, body)

    assert final_metadata is metadata
    assert final_archive.stat().st_size == ceiling
    with tarfile.open(final_archive) as tar:
        assert tar.getnames()[0] == RESULT_METADATA_MEMBER


def test_finalize_embed_failure_fails_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finalize 写失败（IO/tar 族）→ 诚实判败：failed metadata 随可提交的
    metadata-only 归档上报，不重试不死循环（原 embed 失败臂同语义）。"""
    from worker.upload import result_manifest

    def failing_embed(archive: Path, metadata: dict, max_bytes: int = 0) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(result_manifest, "embed_result_metadata", failing_embed)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root))
    queue.shutdown()

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["status"] == "failed"
    assert "result metadata finalize failed" in report["error_message"]
    assert report["output_artifacts"] == {}
    # 判败上报成功（204）：marker 与执行目录照常收尾。
    assert not (work_root / "exec-1").exists()
