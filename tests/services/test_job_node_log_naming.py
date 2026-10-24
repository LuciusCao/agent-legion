"""#1113 / #1340：节点日志命名
``by-job/<shard>/<job_id>/<enc(node_key)>[.shard-<i>].log`` 的单射性、node_key
编码、与旧扁平名的命名空间隔离，以及删除侧清理新旧 job 独占目录时不误删兄弟日志。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

import pytest

from server.app.job_node_log_names import job_node_log_dir_names
from server.app.jobs.storage_layout import job_shard
from server.app.services.job_deletion_trash import purge_deleted_job_files
from server.app.services.job_log_paths import resolve_job_log_path
from server.app.settings import Settings
from server.app.storage_paths import (
    JOB_NODE_LOG_ROOT,
    job_log_dir,
    job_node_log_dir_name,
    job_node_log_name,
    legacy_job_node_log_name,
)

pytestmark = pytest.mark.no_db

_A = "ws_wf_A"

# issue #1113 的两个碰撞例子：(job_id, node_key, shard_index) 两两成对。
_ISSUE_COLLISIONS = {
    "节点 x-y 与 job A-x 的节点 y": ((_A, "x-y", None), (f"{_A}-x", "y", None)),
    "分片 x#1 与 job A-x 的普通节点 shard-1": ((_A, "x", 1), (f"{_A}-x", "shard-1", None)),
}


@pytest.mark.parametrize(
    ("left", "right"), list(_ISSUE_COLLISIONS.values()), ids=list(_ISSUE_COLLISIONS)
)
def test_issue_collision_pairs_get_distinct_names(
    left: tuple[str, str, int | None], right: tuple[str, str, int | None]
) -> None:
    # 前提：旧扁平名确实同名（本用例钉的就是这一缺陷）。
    assert legacy_job_node_log_name(*left) == legacy_job_node_log_name(*right)
    assert job_node_log_name(*left) != job_node_log_name(*right)
    # 两者落在各自 job 的独占目录。
    assert job_node_log_name(*left).startswith(f"{job_node_log_dir_name(left[0])}/")
    assert job_node_log_name(*right).startswith(f"{job_node_log_dir_name(right[0])}/")


_TRICKY_KEYS = (
    "x",
    "x-y",
    "y",
    "shard-1",
    "x.shard-1",
    "x-shard-1",
    "x.y",
    "a/b",
    "a%2Fb",
    "a%2Eb",
    "..",
    ".",
    ".hidden",
    "节点",
    "sp ace",
    "tab\tnul\0",
)
_TRICKY_JOBS = (_A, f"{_A}-x", f"{_A}-x-y", f"{_A}.log", f"{_A}-x.shard-1")


def test_naming_is_injective_over_tricky_inputs() -> None:
    triples = [
        (job, key, shard)
        for job in _TRICKY_JOBS
        for key in _TRICKY_KEYS
        for shard in (None, 0, 1, 10)
    ]
    names = [job_node_log_name(*triple) for triple in triples]
    assert len(set(names)) == len(triples)


def test_job_log_dirs_share_job_shard_and_keep_legacy_candidate() -> None:
    assert job_node_log_dir_names(_A) == (
        f"{JOB_NODE_LOG_ROOT}/{job_shard(_A)}/{_A}",
        f"{JOB_NODE_LOG_ROOT}/{_A}",
    )


@pytest.mark.parametrize("node_key", _TRICKY_KEYS)
@pytest.mark.parametrize("shard_index", [None, 0, 12])
def test_node_key_encoding_is_one_safe_reversible_component(
    node_key: str, shard_index: int | None
) -> None:
    """node_key 含 ``/`` / ``.`` / ``..`` 时仍只产生 job 目录下的一个文件名分量：
    不建子目录（无空目录遗留）、不逃出 job 目录，且可逆还原。"""
    name = PurePosixPath(job_node_log_name(_A, node_key, shard_index))

    assert name.parts[:3] == (JOB_NODE_LOG_ROOT, job_shard(_A), _A)
    assert len(name.parts) == 4
    stem = name.name.removesuffix(".log")
    if shard_index is not None:
        stem = stem.removesuffix(f".shard-{shard_index}")
    assert "." not in stem and "/" not in stem
    assert unquote(stem) == node_key


def test_non_ascii_key_is_not_inflated() -> None:
    """#1215 codex P1：非 ASCII 字符原样保留，不按 UTF-8 字节膨胀 3 倍——
    旧命名能写下的中文 key，新命名同样写得下。"""
    key = "节" * 28  # 84 字节；百分号编码会膨胀到 252 字节
    name = PurePosixPath(job_node_log_name(_A, key, 12))

    assert name.name == f"{key}.shard-12.log"
    assert len(name.name.encode()) <= 255


_LONG_KEYS = {
    "ASCII 超长": "k" * 300,
    "中文超长": "节" * 120,
    "点号超长（每个 . 编为 3 字节）": "." * 100,
    "恰在边界": "k" * (255 - len(".log")),
    "边界加一": "k" * (255 - len(".log") + 1),
}


@pytest.mark.parametrize("node_key", list(_LONG_KEYS.values()), ids=list(_LONG_KEYS))
@pytest.mark.parametrize("shard_index", [None, 0, 123456])
def test_file_name_is_bounded_by_name_max(node_key: str, shard_index: int | None) -> None:
    name = PurePosixPath(job_node_log_name(_A, node_key, shard_index))

    assert len(name.parts) == 4
    assert len(name.name.encode()) <= 255
    assert name.name.endswith(".log")


def test_bounded_names_stay_injective() -> None:
    """有界形态（前缀 + sha256）之间、与常规形态之间互不相等：同前缀的长 key
    由摘要区分；分片后缀不同也不撞。"""
    keys = ["k" * 300, "k" * 301, "k" * 300 + "x", "k" * 251, "k" * 250, "节" * 120, "节" * 121]
    triples = [(_A, key, shard) for key in keys for shard in (None, 0, 1, 99999)]
    names = [job_node_log_name(*triple) for triple in triples]
    assert len(set(names)) == len(triples)


def test_new_names_never_alias_legacy_flat_names() -> None:
    """旧扁平名都是 ``logs/jobs`` 直属的 ``*.log`` 文件；新名都在 ``by-job/``
    之下——``by-job`` 本身不以 ``.log`` 结尾，两套命名空间不相交。"""
    assert not JOB_NODE_LOG_ROOT.endswith(".log")
    for job in _TRICKY_JOBS:
        for key in _TRICKY_KEYS:
            for shard in (None, 3):
                assert "/" not in legacy_job_node_log_name(job, key.replace("/", "_"), shard)
                assert job_node_log_name(job, key, shard).startswith(f"{JOB_NODE_LOG_ROOT}/")


@pytest.mark.parametrize("job_id", ["", ".", "..", "a/b", "a\0b"])
def test_job_log_dir_name_rejects_ids_outside_invariant(job_id: str) -> None:
    with pytest.raises(ValueError):
        job_node_log_dir_name(job_id)


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


class _UnlockedJobDB:
    @contextmanager
    def job_mutation_lock(self, job_id: str) -> Any:
        yield False


def _write(settings: Settings, triple: tuple[str, str, int | None]) -> Path:
    path = job_log_dir(settings.logs_dir) / job_node_log_name(*triple)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(repr(triple), encoding="utf-8")
    return path


@pytest.mark.parametrize("deleted_side", [0, 1], ids=["删左", "删右"])
@pytest.mark.parametrize(
    ("left", "right"), list(_ISSUE_COLLISIONS.values()), ids=list(_ISSUE_COLLISIONS)
)
def test_deleting_one_side_of_issue_collision_keeps_the_other(
    tmp_path: Path,
    left: tuple[str, str, int | None],
    right: tuple[str, str, int | None],
    deleted_side: int,
) -> None:
    """写入不再互相覆盖；删除碰撞对任一方的 job 只移走它自己的目录。"""
    settings = _settings(tmp_path)
    pair = (left, right)
    paths = [_write(settings, triple) for triple in pair]
    deleted, kept = pair[deleted_side], pair[1 - deleted_side]
    run_logs = [(deleted[1], f"logs/jobs/{job_node_log_name(*deleted)}")]

    assert (
        purge_deleted_job_files(_UnlockedJobDB(), {"id": deleted[0]}, settings, "op-1", run_logs)
        is False
    )

    assert not paths[deleted_side].exists()
    assert not (job_log_dir(settings.logs_dir) / job_node_log_dir_name(deleted[0])).exists()
    assert paths[1 - deleted_side].read_text(encoding="utf-8") == repr(kept)


def test_purge_removes_whole_job_dir_including_unrecorded_logs(tmp_path: Path) -> None:
    """新命名整目录清理：没有 node_runs 行的日志（如已退出图的历史节点）也一并
    移走，job 目录不留空壳；同前缀兄弟 job 的目录不动。"""
    settings = _settings(tmp_path)
    own = [_write(settings, (_A, key, shard)) for key in ("x", "a/b") for shard in (None, 2)]
    sibling = _write(settings, (f"{_A}-x", "y", None))

    purge_deleted_job_files(_UnlockedJobDB(), {"id": _A}, settings, "op-1")

    assert not any(path.exists() for path in own)
    assert not (job_log_dir(settings.logs_dir) / job_node_log_dir_name(_A)).exists()
    assert sibling.is_file()


def test_purge_removes_coexisting_new_and_legacy_dirs_for_same_job(tmp_path: Path) -> None:
    """升级前后同一 job_id 的目录并存时，两者都完整清理，兄弟 job 两代目录不动。"""
    settings = _settings(tmp_path)
    sibling_id = f"{_A}-x"
    own_new = _write(settings, (_A, "new", None))
    sibling_new = _write(settings, (sibling_id, "new", None))
    own_legacy = job_log_dir(settings.logs_dir) / job_node_log_dir_names(_A)[1] / "old.log"
    sibling_legacy = (
        job_log_dir(settings.logs_dir) / job_node_log_dir_names(sibling_id)[1] / "old.log"
    )
    for path, content in ((own_legacy, "own old"), (sibling_legacy, "sibling old")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    purge_deleted_job_files(_UnlockedJobDB(), {"id": _A}, settings, "op-1")

    assert not own_new.exists()
    assert not own_legacy.exists()
    assert not own_new.parent.exists()
    assert not own_legacy.parent.exists()
    assert sibling_new.is_file()
    assert sibling_legacy.read_text(encoding="utf-8") == "sibling old"


def test_log_api_resolves_new_layout_path(tmp_path: Path) -> None:
    """读取侧按 node_runs.log_path 原样解析：新布局在 ``logs/jobs`` 之下即放行。"""
    settings = _settings(tmp_path)
    path = _write(settings, (_A, "a/b", 1))
    stored = f"logs/jobs/{job_node_log_name(_A, 'a/b', 1)}"

    assert resolve_job_log_path(stored, settings) == path.resolve()


def test_log_api_resolves_legacy_by_job_path_without_migration(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    stored = f"logs/jobs/{job_node_log_dir_names(_A)[1]}/old.log"
    path = settings.data_dir / stored
    path.parent.mkdir(parents=True)
    path.write_text("old", encoding="utf-8")

    assert resolve_job_log_path(stored, settings) == path.resolve()
