"""落点形态预检（#759 对抗复审 P2 族）：跨通道前缀互斥 + 祖先畅通 + 保
留源保护。

纯路径数学（tmp_path 现场 + stat），不碰数据库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent_control.completion_preflight import (
    blocking_ancestor,
    find_landing_conflict,
    gate_safe_staged_moves,
)

pytestmark = pytest.mark.no_db


def _move(job_dir: Path, rel: str) -> tuple[Path, Path]:
    return (job_dir / rel, job_dir / ".staging" / rel)


def _log_move(tmp_path: Path) -> tuple[Path, Path]:
    """kind=code 结果的 node.log move：target 在 logs 树，source 在 staging 根。"""
    return (tmp_path / "logs" / "node_a.log", tmp_path / "view" / "node.log")


def _conflict(
    tmp_path: Path,
    staged_moves: list[tuple[Path, Path]],
    remote_landing_names: tuple[str, ...],
    *,
    job_dir: Path | None = None,
) -> str | None:
    return find_landing_conflict(
        job_dir=job_dir or tmp_path / "job",
        view_dir=tmp_path / "view",
        staged_moves=staged_moves,
        remote_landing_names=remote_landing_names,
    )


def test_prefix_clash_between_archive_and_remote_ref(tmp_path: Path) -> None:
    conflict = _conflict(
        tmp_path, [_move(tmp_path, "reports")], ("reports/out.json",), job_dir=tmp_path
    )

    assert conflict is not None
    assert "conflicting output paths" in conflict
    assert "reports" in conflict


def test_prefix_clash_between_remote_refs(tmp_path: Path) -> None:
    conflict = _conflict(tmp_path, [], ("reports", "reports/out.json"))

    assert conflict is not None
    assert "conflicting output paths" in conflict


def test_same_name_across_channels_is_not_a_clash(tmp_path: Path) -> None:
    """同名（非真前缀）是合法的冗余报告——remote 通道靠后段 dedup 胜出。"""
    assert _conflict(tmp_path, [_move(tmp_path, "out.json")], ("out.json",)) is None


def test_siblings_and_shared_directories_pass(tmp_path: Path) -> None:
    assert (
        _conflict(
            tmp_path,
            [_move(tmp_path, "reports/a.json"), _move(tmp_path, "reports/b.json")],
            ("reports-2/c.json", "reports/c.json"),
        )
        is None
    )


def test_log_move_target_stays_out_of_landing_set(tmp_path: Path) -> None:
    """node.log 的 target 落 logs 树，不进落点集——与 job_dir 的 logs 名不判。"""
    assert _conflict(tmp_path, [_log_move(tmp_path)], ("logs",)) is None


def test_remote_ref_on_reserved_log_source_conflicts(tmp_path: Path) -> None:
    """P2-B：remote 落点名 == 保留源名（node.log）——overwrite 遍的 spot
    unlink 会抹掉 log source，闸内 FileNotFoundError 把成功节点判 failed。"""
    conflict = _conflict(tmp_path, [_log_move(tmp_path)], ("node.log",))

    assert conflict is not None
    assert "reserved result member" in conflict


def test_remote_ref_below_reserved_log_source_conflicts(tmp_path: Path) -> None:
    """P2-B 变体：remote 落点名以保留源名为真前缀——blocker unlink 同样抹源。"""
    conflict = _conflict(tmp_path, [_log_move(tmp_path)], ("node.log/x",))

    assert conflict is not None
    assert "reserved result member" in conflict


def test_archive_output_named_like_log_member_conflicts(tmp_path: Path) -> None:
    """P2-B 同族：expected 输出名撞 CODE_RESULT_LOG_MEMBER——「同 source
    双 move」病态声明，闸内必炸，预检提前判死。"""
    job_dir = tmp_path / "job"
    staged = [(job_dir / "node.log", tmp_path / "view" / "node.log"), _log_move(tmp_path)]

    conflict = _conflict(tmp_path, staged, (), job_dir=job_dir)

    assert conflict is not None
    assert "reserved result member" in conflict


def test_log_member_name_is_free_without_log_move(tmp_path: Path) -> None:
    """agent 节点（无 log move、归档无保留成员）输出名叫 node.log 合法。"""
    assert _conflict(tmp_path, [_move(tmp_path, "node.log")], ()) is None


def test_existing_file_ancestor_blocks(tmp_path: Path) -> None:
    (tmp_path / "reports").write_bytes(b"leftover")
    conflict = _conflict(tmp_path, [_move(tmp_path, "reports/out.json")], (), job_dir=tmp_path)

    assert conflict is not None
    assert "blocked" in conflict


def test_existing_dir_ancestor_and_missing_ancestors_pass(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    assert (
        _conflict(
            tmp_path, [_move(tmp_path, "reports/out.json")], ("other/x.json",), job_dir=tmp_path
        )
        is None
    )


def test_broken_symlink_ancestor_blocks(tmp_path: Path) -> None:
    """破 symlink 同样挡住 mkdir（exists 漏判，lexists 才抓得到）。"""
    (tmp_path / "reports").symlink_to(tmp_path / "nowhere")
    conflict = _conflict(tmp_path, [_move(tmp_path, "reports/out.json")], (), job_dir=tmp_path)

    assert conflict is not None
    assert "blocked" in conflict


def test_gate_safe_staged_moves_filters_only_blocked(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "reports").write_bytes(b"leftover")
    blocked = _move(job_dir, "reports/out.json")
    log_move = (tmp_path / "logs" / "node_a.log", job_dir / ".staging" / "node.log")

    assert gate_safe_staged_moves([blocked, log_move]) == [log_move]


def test_blocking_ancestor_stops_at_first_existing_entry(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    assert blocking_ancestor(tmp_path / "a" / "b" / "c") is None
