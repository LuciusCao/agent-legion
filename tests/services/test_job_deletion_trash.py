"""#958：job 删除 ``.trash`` 的 TTL 回收语义（纯文件系统，不碰 DB）。"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import server.app.services.job_deletion_log_paths as log_paths_module
import server.app.services.job_deletion_trash as trash_module
from server.app.services.job_deletion_trash import (
    COMMITTED_MARKER,
    DELETION_TRASH_TTL,
    deleted_job_log_paths,
    jobs_trash_root,
    logs_trash_root,
    purge_deleted_job_files,
)
from server.app.services.job_deletion_trash_sweep import sweep_deletion_trash
from server.app.settings import Settings
from server.app.storage_paths import (
    JOB_NODE_LOG_ROOT,
    job_node_log_name,
    legacy_job_node_log_name,
)
from server.app.workflow_worker.maintenance import WorkflowMaintenance

pytestmark = pytest.mark.no_db

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
    for name in ["jobs", "logs", "videos", "packages"]:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
    )


def _op_dir(
    root: Path, name: str, age: timedelta, child: str = "payload", *, committed: bool = True
) -> Path:
    op = root / name
    (op / child).mkdir(parents=True)
    (op / child / "f.bin").write_bytes(b"x")
    if committed:
        (op / COMMITTED_MARKER).write_text("job\n", encoding="utf-8")
    stamp = (_NOW - age).timestamp()
    os.utime(op, (stamp, stamp))
    return op


def test_sweep_removes_only_entries_older_than_ttl(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    old_job = _op_dir(jobs_trash_root(settings), "op-old", DELETION_TRASH_TTL + timedelta(hours=1))
    young_job = _op_dir(jobs_trash_root(settings), "op-young", timedelta(minutes=5))
    old_log = _op_dir(logs_trash_root(settings), "op-old", DELETION_TRASH_TTL * 2)

    removed = sweep_deletion_trash(settings, now=_NOW)

    assert removed == 2
    assert not old_job.exists()
    assert not old_log.exists()
    assert (young_job / "payload" / "f.bin").exists()


def test_sweep_never_purges_legacy_unmarked_rollback_copies(tmp_path: Path) -> None:
    """P1：0.7.17 前 _restore_paths 回滚冲突留下的恢复副本无已提交标记，其
    jobs 行仍在、可能是 legacy job 唯一的产物副本——超 TTL 也不自动删除。"""
    settings = _settings(tmp_path)
    legacy = _op_dir(
        jobs_trash_root(settings),
        "old-operation",
        DELETION_TRASH_TTL * 10,
        child="live-job",
        committed=False,
    )
    legacy_log = _op_dir(
        logs_trash_root(settings), "old-operation", DELETION_TRASH_TTL * 10, committed=False
    )
    marked = _op_dir(jobs_trash_root(settings), "op-new", DELETION_TRASH_TTL * 2)

    removed = sweep_deletion_trash(settings, now=_NOW)

    assert removed == 1
    assert not marked.exists()
    assert (legacy / "live-job" / "f.bin").exists()
    assert (legacy_log / "payload" / "f.bin").exists()


def test_sweep_never_touches_symlink_entries(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    root = jobs_trash_root(settings)
    root.mkdir(parents=True)
    link = root / "op-link"
    link.symlink_to(outside, target_is_directory=True)
    stamp = (_NOW - DELETION_TRASH_TTL * 2).timestamp()
    os.utime(link, (stamp, stamp), follow_symlinks=False)

    (outside / COMMITTED_MARKER).write_text("job\n", encoding="utf-8")

    assert sweep_deletion_trash(settings, now=_NOW) == 0

    assert link.is_symlink()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_sweep_without_trash_roots_is_noop(tmp_path: Path) -> None:
    assert sweep_deletion_trash(_settings(tmp_path), now=_NOW) == 0


def test_sweep_survives_single_entry_failure(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    age = DELETION_TRASH_TTL * 2
    first = _op_dir(jobs_trash_root(settings), "op-a", age)
    second = _op_dir(jobs_trash_root(settings), "op-b", age)
    real_rmtree = shutil.rmtree

    def _flaky(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path) == first or first in Path(path).parents:
            raise OSError("busy")
        real_rmtree(path, *args, **kwargs)

    with patch("server.app.services.job_deletion_trash_sweep.shutil.rmtree", side_effect=_flaky):
        removed = sweep_deletion_trash(settings, now=_NOW)

    assert removed == 1
    assert first.exists()
    assert not second.exists()


def test_sweep_keeps_committed_marker_when_purge_fails_midway(tmp_path: Path) -> None:
    """#1065 codex P2：子项删到一半因瞬时错误中止时，`.committed-deletion` 必须仍在
    （标记最后删），否则残余目录会被当成 legacy 恢复副本永久跳过；下一轮照常回收。"""
    settings = _settings(tmp_path)
    entry = _op_dir(jobs_trash_root(settings), "op-flaky", DELETION_TRASH_TTL * 2)
    (entry / "second").mkdir()
    (entry / "second" / "g.bin").write_bytes(b"y")
    stamp = (_NOW - DELETION_TRASH_TTL * 2).timestamp()
    os.utime(entry, (stamp, stamp))  # 加子目录会刷新 entry 的 mtime，重新压回 TTL 之前
    calls: list[Path] = []
    real_rmtree = shutil.rmtree

    def _fail_once(path: Any, *args: Any, **kwargs: Any) -> None:
        calls.append(Path(path))
        if len(calls) == 1:
            raise OSError("transient")
        real_rmtree(path, *args, **kwargs)

    with patch(
        "server.app.services.job_deletion_trash_sweep.shutil.rmtree", side_effect=_fail_once
    ):
        assert sweep_deletion_trash(settings, now=_NOW) == 0
        assert entry.is_dir()
        assert (entry / COMMITTED_MARKER).is_file()

        assert sweep_deletion_trash(settings, now=_NOW) == 1

    assert not entry.exists()


def test_maintenance_runs_trash_sweep(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.config = {}
    maintenance = WorkflowMaintenance(MagicMock(), settings)

    with (
        patch("server.app.workflow_worker.maintenance.cleanup_old_logs", return_value=(0, 0)),
        patch(
            "server.app.workflow_worker.maintenance.sweep_deletion_trash", return_value=3
        ) as sweep,
    ):
        maintenance._run_cleanup()

    sweep.assert_called_once_with(settings)


class _UnlockedJobDB:
    """锁下复核恒为「未重建」的最小 JobDB 替身；``in_lock`` 供探针读取。"""

    in_lock = False

    @contextmanager
    def job_mutation_lock(self, job_id: str) -> Any:
        self.in_lock = True
        try:
            yield False
        finally:
            self.in_lock = False


def test_purge_derives_log_paths_outside_lock_and_never_lists_log_dir(tmp_path: Path) -> None:
    """#1065 codex P2：日志路径在锁外推导，锁内只复核行并 rename；推导按快照
    精确生成，从不列举共享日志目录。"""
    settings = _settings(tmp_path)
    log_dir = settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True)
    (log_dir / "job-x-n1.log").write_text("log", encoding="utf-8")
    (log_dir / "job-x-n1-shard-0.log").write_text("shard", encoding="utf-8")
    new_log = log_dir / job_node_log_name("job-x", "n1")
    new_log.parent.mkdir(parents=True)
    new_log.write_text("new", encoding="utf-8")
    job_db = _UnlockedJobDB()
    derived_in_lock: list[bool] = []
    listed: list[Path] = []
    real_derive = trash_module.deleted_job_log_paths
    real_scandir = trash_module.os.scandir

    def _spy_derive(*args: Any) -> list[Path]:
        derived_in_lock.append(job_db.in_lock)
        return real_derive(*args)

    def _spy_scandir(path: Any) -> Any:
        # rmtree 清掉已移入 trash 的 job 日志目录时按 fd（int）scandir。
        if not isinstance(path, int):
            listed.append(Path(path))
        return real_scandir(path)

    with (
        patch.object(trash_module, "deleted_job_log_paths", _spy_derive),
        patch.object(trash_module.os, "scandir", _spy_scandir),
    ):
        recreated = purge_deleted_job_files(
            job_db,
            {"id": "job-x", "storage_dir": "job-x"},
            settings,
            "op-1",
            [("n1", "logs/jobs/job-x-n1.log"), ("n1", "logs/jobs/job-x-n1-shard-0.log")],
        )

    assert recreated is False
    assert derived_in_lock == [False]
    assert log_dir not in listed
    assert log_dir / JOB_NODE_LOG_ROOT not in listed
    assert sorted(p.name for p in log_dir.iterdir()) == [JOB_NODE_LOG_ROOT]
    assert list((log_dir / JOB_NODE_LOG_ROOT).iterdir()) == []


# 删除路径模型（PR #1065 codex 4203283377）：被删 job 与按命名规则可能碰撞的
# 兄弟 job。job id 照 _job_id 拼成 ``<workspace>_<workflow>_<source_id>``，
# source_id 可含连字符；节点 key 也可含连字符与 ``-shard-<n>``。#1113 起旧扁平名
# （含普通日志）只从快照的 node_runs.(node_key, log_path) 精确推导。
_JOB = "ws_wf_Q030"
_OWN = {
    "普通节点日志": legacy_job_node_log_name(_JOB, "extract_question"),
    "普通节点 x 的日志": legacy_job_node_log_name(_JOB, "x"),
    "分片日志 0": legacy_job_node_log_name(_JOB, "extract_question", 0),
    "分片日志多位索引": legacy_job_node_log_name(_JOB, "extract_question", 12),
    "旧代次分片日志（node_shards 已删、node_runs 仍在）": legacy_job_node_log_name(
        _JOB, "extract_question", 7
    ),
    "含 -shard- 的节点 key 普通日志": legacy_job_node_log_name(_JOB, "split-shard-0"),
    "含 -shard- 的节点 key 分片日志": legacy_job_node_log_name(_JOB, "split-shard-0", 3),
}
_RUN_LOGS = (
    ("extract_question", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'extract_question')}"),
    ("extract_question", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'extract_question', 0)}"),
    ("extract_question", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'extract_question', 12)}"),
    ("extract_question", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'extract_question', 7)}"),
    ("split-shard-0", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'split-shard-0')}"),
    ("split-shard-0", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'split-shard-0', 3)}"),
    ("x", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'x')}"),
    # 不可推导 / 越界的历史 log_path 一律不删（下面的兄弟文件因此保留）。
    ("x", ""),
    ("x", "/outside/elsewhere.log"),
    ("x", f"jobs/{legacy_job_node_log_name(_JOB, 'x')}"),
    ("x", f"logs/jobs/sub/{legacy_job_node_log_name(_JOB, 'x')}"),
)
_SIBLING = {
    # codex 原例：source ``Q030-extract_question-shard-0`` 的普通 / 分片日志。
    "source 形如 <src>-<k>-shard-0 的普通日志": legacy_job_node_log_name(
        f"{_JOB}-extract_question-shard-0", "extract_answer"
    ),
    "source 形如 <src>-<k>-shard-0 的分片日志": legacy_job_node_log_name(
        f"{_JOB}-extract_question-shard-0", "extract_answer", 1
    ),
    "source 形如 <src>-x 的普通日志": legacy_job_node_log_name(f"{_JOB}-x", "extract_question"),
    "source 形如 <src>-x 的分片日志": legacy_job_node_log_name(f"{_JOB}-x", "extract_question", 0),
    # 被删 job 有普通节点 x；兄弟 <src>-x 的节点 shard-1 写 <job>-x-shard-1.log。
    "兄弟节点 key 形如 shard-<n> 的普通日志": legacy_job_node_log_name(f"{_JOB}-x", "shard-1"),
    "兄弟节点 key 也含 -shard-<n> 的分片日志": legacy_job_node_log_name(
        f"{_JOB}-extract_question-shard-0", "x-shard-1", 2
    ),
    # #1113：被删 job 的节点 ``x-y`` 若从未运行（无 node_runs 行），同名的
    # ``<job>-x-y.log`` 属于兄弟 job ``<job>-x`` 的节点 ``y``——不再按 job_nodes
    # 的节点 key 生成旧名去删。
    "被删 job 未运行节点与兄弟日志同名": legacy_job_node_log_name(f"{_JOB}-x", "y"),
    "被删 job 未写过的分片索引": legacy_job_node_log_name(_JOB, "extract_question", 5),
    "无连字符前缀相同的 job": legacy_job_node_log_name(f"{_JOB}1", "extract_question", 0),
    "非命名函数产出的前导零索引": f"{_JOB}-extract_question-shard-00.log",
    "非数字索引": f"{_JOB}-extract_question-shard-x.log",
}


def _seed_deletion_model(settings: Settings) -> tuple[Path, Path]:
    log_dir = settings.logs_dir / "jobs"
    (log_dir / "sub").mkdir(parents=True)
    for name in (*_OWN.values(), *_SIBLING.values()):
        (log_dir / name).write_text(name, encoding="utf-8")
    (log_dir / "sub" / legacy_job_node_log_name(_JOB, "x")).write_text("sub", encoding="utf-8")
    (settings.jobs_dir / _JOB).mkdir()
    (settings.jobs_dir / legacy_job_node_log_name(_JOB, "x")).write_text(
        "jobs-root", encoding="utf-8"
    )
    (settings.jobs_dir / f"{_JOB}-x").mkdir()
    return log_dir, settings.jobs_dir


def _purge_model(settings: Settings) -> bool:
    return purge_deleted_job_files(_UnlockedJobDB(), {"id": _JOB}, settings, "op-1", _RUN_LOGS)


def test_legacy_job_node_log_names_never_collide_across_model_rows() -> None:
    """模型表前提：本表各行由命名函数生成的文件名两两不同（碰撞即测试设计错误）。"""
    names = [*_OWN.values(), *_SIBLING.values()]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("name", list(_OWN.values()), ids=list(_OWN))
def test_purge_removes_every_owned_log(tmp_path: Path, name: str) -> None:
    settings = _settings(tmp_path)
    log_dir, jobs_dir = _seed_deletion_model(settings)

    assert _purge_model(settings) is False

    assert not (log_dir / name).exists()
    assert not (jobs_dir / _JOB).exists()


@pytest.mark.parametrize("name", list(_SIBLING.values()), ids=list(_SIBLING))
def test_purge_keeps_colliding_sibling_logs(tmp_path: Path, name: str) -> None:
    settings = _settings(tmp_path)
    log_dir, jobs_dir = _seed_deletion_model(settings)

    assert _purge_model(settings) is False

    assert (log_dir / name).read_text(encoding="utf-8") == name
    assert (jobs_dir / f"{_JOB}-x").is_dir()
    # 越界的 node_runs.log_path 不删（不在 logs/jobs 直属层的同名文件）。
    assert (log_dir / "sub" / legacy_job_node_log_name(_JOB, "x")).is_file()
    assert (jobs_dir / legacy_job_node_log_name(_JOB, "x")).is_file()


def test_run_log_row_must_match_its_own_node_key(tmp_path: Path) -> None:
    """node_key 等值守卫：快照行 ``("q", <J>-x-y-shard-0.log)`` 的文件名按 ``x-y``
    才能生成，与该行 key ``q`` 不符——那是兄弟 job ``<J>-x`` 的节点 ``y-shard-0``
    的日志，保留。"""
    settings = _settings(tmp_path)
    log_dir = settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True)
    sibling = log_dir / legacy_job_node_log_name(f"{_JOB}-x", "y-shard-0")
    sibling.write_text("sibling", encoding="utf-8")
    assert sibling.name == legacy_job_node_log_name(_JOB, "x-y", 0)

    paths = deleted_job_log_paths(settings, _JOB, [("q", f"logs/jobs/{sibling.name}")])

    assert paths == []
    _purge_with(settings, [("q", f"logs/jobs/{sibling.name}")])
    assert sibling.read_text(encoding="utf-8") == "sibling"


def test_run_log_row_outside_log_dir_is_not_mapped_to_log_dir(tmp_path: Path) -> None:
    """父目录守卫：log_path 在 ``logs/jobs/sub/`` 下时不删，也不按同名映射到
    ``logs/jobs`` 直属层（那里的同名文件属于兄弟 job ``<J>-x`` 的节点 shard-0）。"""
    settings = _settings(tmp_path)
    log_dir = settings.logs_dir / "jobs"
    (log_dir / "sub").mkdir(parents=True)
    name = legacy_job_node_log_name(_JOB, "x", 0)
    (log_dir / "sub" / name).write_text("sub", encoding="utf-8")
    (log_dir / name).write_text("sibling", encoding="utf-8")

    _purge_with(settings, [("x", f"logs/jobs/sub/{name}")])

    assert (log_dir / name).read_text(encoding="utf-8") == "sibling"
    assert (log_dir / "sub" / name).read_text(encoding="utf-8") == "sub"


def _purge_with(settings: Settings, run_logs: list[tuple[str, str]]) -> bool:
    return purge_deleted_job_files(_UnlockedJobDB(), {"id": _JOB}, settings, "op-1", run_logs)


_LONG_KEY = "k" * 260  # 文件名超 NAME_MAX（255）：stat 抛 ENAMETOOLONG


def test_deleted_job_log_paths_treats_unprobeable_names_as_missing(tmp_path: Path) -> None:
    """Python 3.13 的 Path.exists 对 ENAMETOOLONG 会抛：逐路径视为不存在并告警，
    其余日志照常推导，不让已提交的删除变成 API 错误。"""
    settings = _settings(tmp_path)
    log_dir = settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True)
    (log_dir / legacy_job_node_log_name(_JOB, "n1")).write_text("log", encoding="utf-8")

    paths = deleted_job_log_paths(
        settings,
        _JOB,
        [
            (_LONG_KEY, f"logs/jobs/{legacy_job_node_log_name(_JOB, _LONG_KEY, 0)}"),
            ("n1", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'n1')}"),
        ],
    )

    assert paths == [log_dir / legacy_job_node_log_name(_JOB, "n1")]


def test_deleted_job_log_paths_skips_run_log_whose_parents_form_symlink_loop(
    tmp_path: Path,
) -> None:
    """#1065 codex P2：历史 log_path 的父目录形成符号链接环时 Path.resolve 抛
    RuntimeError，按不可解析处理（不删、不中断），其余日志照常推导。"""
    settings = _settings(tmp_path)
    log_dir = settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True)
    (log_dir / legacy_job_node_log_name(_JOB, "n1")).write_text("log", encoding="utf-8")
    real_resolve = log_paths_module.resolve_data_path

    def _looping(raw: str, *args: Any, **kwargs: Any) -> Any:
        if "loop" in raw:
            raise RuntimeError("Symlink loop from '/data/logs/loop'")
        return real_resolve(raw, *args, **kwargs)

    with patch.object(log_paths_module, "resolve_data_path", _looping):
        paths = deleted_job_log_paths(
            settings,
            _JOB,
            [
                ("n2", f"logs/loop/jobs/{legacy_job_node_log_name(_JOB, 'n2', 0)}"),
                ("n1", f"logs/jobs/{legacy_job_node_log_name(_JOB, 'n1')}"),
            ],
        )

    assert paths == [log_dir / legacy_job_node_log_name(_JOB, "n1")]


def test_purge_stages_remaining_logs_when_one_path_fails_under_lock(tmp_path: Path) -> None:
    """锁内逐路径处理：某个路径的探测 / rename 抛 OSError 只跳过它本身。"""
    settings = _settings(tmp_path)
    log_dir = settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True)
    good = log_dir / legacy_job_node_log_name(_JOB, "n1")
    good.write_text("log", encoding="utf-8")
    unprobeable = log_dir / legacy_job_node_log_name(_JOB, _LONG_KEY)

    with patch.object(trash_module, "deleted_job_log_paths", return_value=[unprobeable, good]):
        recreated = purge_deleted_job_files(_UnlockedJobDB(), {"id": _JOB}, settings, "op-1")

    assert recreated is False
    assert not good.exists()


def test_purge_reports_recreated_when_lock_fails_before_recheck(tmp_path: Path) -> None:
    """#1065 codex P2：锁事务在给出存在性结果前失败（瞬时 DB 错误）时，无法
    排除同源重建，必须按「已重建」返回，让调用方跳过按 id 的清理与广播。"""
    settings = _settings(tmp_path)
    job_dir = settings.jobs_dir / "job-y"
    job_dir.mkdir(parents=True)

    class _JobDB:
        @contextmanager
        def job_mutation_lock(self, job_id: str) -> Any:
            raise RuntimeError("connection lost")
            yield False  # pragma: no cover

    recreated = purge_deleted_job_files(
        _JobDB(), {"id": "job-y", "storage_dir": "job-y"}, settings, "op-1"
    )

    assert recreated is True
    assert job_dir.is_dir()


def test_purge_keeps_recheck_result_when_lock_fails_after_yield(tmp_path: Path) -> None:
    """yield 之后（如 commit）才失败：复核值已产出，照常返回真实结果。"""
    settings = _settings(tmp_path)

    class _JobDB:
        @contextmanager
        def job_mutation_lock(self, job_id: str) -> Any:
            yield False
            raise RuntimeError("commit failed")

    recreated = purge_deleted_job_files(
        _JobDB(), {"id": "job-z", "storage_dir": "job-z"}, settings, "op-1"
    )

    assert recreated is False
