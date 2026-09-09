"""Result validate process pool (issue #569): offload correctness and isolation.

Mirrors the unpack-pool tests (#552): real validation through the pool,
exception types surviving the process boundary, BrokenProcessPool
self-healing, and the size-resolution precedence (env > instance setting >
auto).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.app.agent_broker.result_validate_pool import (
    validate_in_pool,
    validate_skill_commit_outputs,
)
from server.app.skills.errors import SkillRepoError
from tests.helpers.skill_git import _KEY, _head_commit, _make_skill_repo

pytestmark = pytest.mark.no_db


def _task_args(tmp_path: Path, *, validate_script: str = "import sys; sys.exit(0)\n"):
    repo = _make_skill_repo(tmp_path / "skills", _KEY, validate_script=validate_script)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    return (
        str(tmp_path / "skills"),
        str(tmp_path / "runs"),
        ("git",),
        _KEY,
        _head_commit(repo),
        str(job_dir),
    )


def test_validate_in_pool_passes_valid_outputs(tmp_path: Path) -> None:
    assert validate_in_pool(validate_skill_commit_outputs, *_task_args(tmp_path)) is None


def test_failing_validator_verdict_survives_the_process_boundary(tmp_path: Path) -> None:
    args = _task_args(
        tmp_path, validate_script="import sys; sys.stderr.write('bad output\\n'); sys.exit(1)\n"
    )

    error = validate_in_pool(validate_skill_commit_outputs, *args)

    assert error is not None
    assert "Output validation failed" in error
    assert "bad output" in error


def test_materialization_error_type_survives_the_process_boundary(
    tmp_path: Path,
) -> None:
    args = _task_args(tmp_path)
    bad = (*args[:4], "0" * 40, args[5])

    with pytest.raises(SkillRepoError, match="missing"):
        validate_in_pool(validate_skill_commit_outputs, *bad)

    # 任务失败炸的是子进程内的一次调用——池必须继续可用。
    assert validate_in_pool(validate_skill_commit_outputs, *args) is None


def _crash_hard() -> None:
    """杀掉自己所在的池 worker（模拟 validator 子进程 OOM / native 崩溃）。"""
    import os

    os._exit(1)


def test_broken_pool_rebuilds_and_recovers(tmp_path: Path) -> None:
    """池 worker 硬死 → BrokenProcessPool 自愈：本任务重试一次仍崩则上抛
    （该 result 判 failed，由 worker_output_validation 的宽捕获兜底），但池
    已重建——下一个正常任务照常完成，不进入「全部 result 永久失败」稳态。"""
    from concurrent.futures.process import BrokenProcessPool

    from server.app.agent_broker import result_validate_pool as pool_module

    try:
        with pytest.raises(BrokenProcessPool):
            validate_in_pool(_crash_hard)
        assert validate_in_pool(validate_skill_commit_outputs, *_task_args(tmp_path)) is None
    finally:
        pool_module.reset_pool()


def test_invalid_workers_env_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from server.app.agent_broker import result_validate_pool as pool_module

    monkeypatch.setenv("AGENT_LEGION_RESULT_VALIDATE_WORKERS", "not-a-number")
    with caplog.at_level("WARNING"):
        size = pool_module._pool_size()
    assert size == min(4, os.cpu_count() or 1)
    assert "AGENT_LEGION_RESULT_VALIDATE_WORKERS" in caplog.text


def test_configure_drives_pool_size_below_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """优先级（同 #554 链路）：env 覆盖 > configure（实例设置）> 自动
    min(4, 核数）。纯尺寸解析，不建池（active_children 纪律：不留进程）。"""
    from server.app.agent_broker import result_validate_pool as pool_module

    monkeypatch.delenv("AGENT_LEGION_RESULT_VALIDATE_WORKERS", raising=False)
    monkeypatch.setattr(pool_module, "_CONFIGURED_WORKERS", 0)
    assert pool_module._pool_size() == min(4, os.cpu_count() or 1)

    pool_module.configure(8)
    assert pool_module._pool_size() == 8

    monkeypatch.setenv("AGENT_LEGION_RESULT_VALIDATE_WORKERS", "3")
    assert pool_module._pool_size() == 3

    pool_module.configure(0)
    monkeypatch.delenv("AGENT_LEGION_RESULT_VALIDATE_WORKERS")
    assert pool_module._pool_size() == min(4, os.cpu_count() or 1)


def test_reset_pool_only_kills_the_broken_instance() -> None:
    """身份守卫：撞破旧池的线程只重置旧池——别人刚建好的新池不受影响
    （完成波下并发撞池的重试 future 不被 cancel）。"""
    from server.app.agent_broker import result_validate_pool as pool_module

    try:
        pool_a = pool_module._pool()
        pool_module.reset_pool(broken=object())  # 非当前池身份 → 不动
        assert pool_module._pool() is pool_a
        pool_module.reset_pool(broken=pool_a)  # 身份匹配 → 关停置 None
        assert pool_module._POOL is None
    finally:
        pool_module.reset_pool()
