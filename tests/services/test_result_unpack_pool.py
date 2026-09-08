"""Result unpack process pool (issue #552): offload correctness and isolation.

``unpack_in_pool`` runs ``unpack_agent_result`` on a ProcessPoolExecutor so
the tar/gzip CPU segment stops convoying the HTTP plane's GIL. These tests
pin: real archive promotion through the pool, worker-side exception types
surviving the process boundary, and pool survival after a failed task.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from server.app.agent_broker.result_unpack import unpack_agent_result
from server.app.agent_broker.result_unpack_pool import unpack_in_pool

pytestmark = pytest.mark.no_db


def _make_archive(path: Path, members: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_unpack_in_pool_promotes_expected_outputs(tmp_path: Path) -> None:
    archive = tmp_path / "result.tar.gz"
    _make_archive(
        archive,
        {
            "output.json": json.dumps({"ok": True}),
            "runs/node_a/worker/events.jsonl": "{}\n",
        },
    )
    job_dir = tmp_path / "jobdir"
    job_dir.mkdir()

    unpack_in_pool(unpack_agent_result, archive, job_dir, ("output.json",), "runs/node_a/worker")

    assert json.loads((job_dir / "output.json").read_text()) == {"ok": True}
    assert (job_dir / "runs" / "node_a" / "worker" / "events.jsonl").is_file()


def test_unpack_in_pool_surfaces_bad_archive_and_pool_survives(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a gzip stream at all")
    job_dir = tmp_path / "jobdir"
    job_dir.mkdir()

    # 坏 tar 的异常族跨进程原样回传（gzip/tarfile 层），主进程不受波及。
    with pytest.raises((OSError, tarfile.TarError)):
        unpack_in_pool(unpack_agent_result, bad, job_dir, (), "")

    # 坏包炸的是子进程内的一次调用——池必须继续可用（下个好包照常解）。
    archive = tmp_path / "good.tar.gz"
    _make_archive(archive, {"o.json": "{}"})
    unpack_in_pool(unpack_agent_result, archive, job_dir, ("o.json",), "")
    assert (job_dir / "o.json").is_file()


def _crash_hard() -> None:
    """杀掉自己所在的池 worker（模拟 zip bomb OOM / native 崩溃）。"""
    import os

    os._exit(1)


def test_broken_pool_rebuilds_and_recovers(tmp_path: Path) -> None:
    """池 worker 硬死 → BrokenProcessPool 自愈：本任务重试一次仍崩则上抛
    （该 result 判 failed，由 completion 的宽捕获兜底），但池已重建——
    下一个正常任务照常完成，不进入「全部 result 永久失败」稳态。"""
    from concurrent.futures.process import BrokenProcessPool

    from server.app.agent_broker import result_unpack_pool as pool_module

    try:
        with pytest.raises(BrokenProcessPool):
            unpack_in_pool(_crash_hard)
        # 上一次提交留下的已是重建后的池：好包照常解。
        archive = tmp_path / "good.tar.gz"
        _make_archive(archive, {"o.json": "{}"})
        job_dir = tmp_path / "jobdir"
        job_dir.mkdir()
        unpack_in_pool(unpack_agent_result, archive, job_dir, ("o.json",), "")
        assert (job_dir / "o.json").is_file()
    finally:
        pool_module.reset_pool()


def test_invalid_workers_env_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from server.app.agent_broker import result_unpack_pool as pool_module

    monkeypatch.setenv("AGENT_LEGION_RESULT_UNPACK_WORKERS", "not-a-number")
    with caplog.at_level("WARNING"):
        size = pool_module._pool_size()
    assert size == min(4, os.cpu_count() or 1)
    assert "AGENT_LEGION_RESULT_UNPACK_WORKERS" in caplog.text


def test_reset_pool_only_kills_the_broken_instance() -> None:
    """身份守卫（复审二轮 P2）：撞破旧池的线程只重置旧池——别人刚建好的
    新池不受影响（完成波下并发撞池的重试 future 不被 cancel）。"""
    from server.app.agent_broker import result_unpack_pool as pool_module

    try:
        pool_a = pool_module._pool()
        pool_module.reset_pool(broken=object())  # 非当前池身份 → 不动
        assert pool_module._pool() is pool_a
        pool_module.reset_pool(broken=pool_a)  # 身份匹配 → 关停置 None
        assert pool_module._POOL is None
    finally:
        pool_module.reset_pool()
