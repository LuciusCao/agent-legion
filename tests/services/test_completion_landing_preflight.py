"""落点形态预检（#759 对抗复审 P2 族）：跨通道前缀互斥 + 祖先畅通 + 保
留源保护；冲突返回落点名集供失败 finish 过滤（codex #774 P2）。

纯路径数学（tmp_path 现场 + stat），不碰数据库。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from server.app.agent_control.completion_moves import (
    blocking_ancestor,
    gate_safe_staged_moves,
)
from server.app.agent_control.completion_preflight import (
    LandingConflict,
    find_landing_conflict,
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
) -> LandingConflict | None:
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
    assert "conflicting output paths" in conflict.message
    assert "reports" in conflict.message
    assert conflict.names == {PurePosixPath("reports"), PurePosixPath("reports/out.json")}


def test_prefix_clash_between_remote_refs(tmp_path: Path) -> None:
    conflict = _conflict(tmp_path, [], ("reports", "reports/out.json"))

    assert conflict is not None
    assert "conflicting output paths" in conflict.message
    assert conflict.names == {PurePosixPath("reports"), PurePosixPath("reports/out.json")}


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
    assert "reserved result member" in conflict.message
    assert conflict.names == {PurePosixPath("node.log")}


def test_remote_ref_below_reserved_log_source_conflicts(tmp_path: Path) -> None:
    """P2-B 变体：remote 落点名以保留源名为真前缀——blocker unlink 同样抹源。"""
    conflict = _conflict(tmp_path, [_log_move(tmp_path)], ("node.log/x",))

    assert conflict is not None
    assert "reserved result member" in conflict.message
    assert conflict.names == {PurePosixPath("node.log/x")}


def test_archive_output_named_like_log_member_conflicts(tmp_path: Path) -> None:
    """P2-B 同族：expected 输出名撞 CODE_RESULT_LOG_MEMBER——「同 source
    双 move」病态声明，闸内必炸，预检提前判死。"""
    job_dir = tmp_path / "job"
    staged = [(job_dir / "node.log", tmp_path / "view" / "node.log"), _log_move(tmp_path)]

    conflict = _conflict(tmp_path, staged, (), job_dir=job_dir)

    assert conflict is not None
    assert "reserved result member" in conflict.message
    assert conflict.names == {PurePosixPath("node.log")}


def test_log_member_name_is_free_without_log_move(tmp_path: Path) -> None:
    """agent 节点（无 log move、归档无保留成员）输出名叫 node.log 合法。"""
    assert _conflict(tmp_path, [_move(tmp_path, "node.log")], ()) is None


def test_existing_file_ancestor_blocks(tmp_path: Path) -> None:
    (tmp_path / "reports").write_bytes(b"leftover")
    conflict = _conflict(tmp_path, [_move(tmp_path, "reports/out.json")], (), job_dir=tmp_path)

    assert conflict is not None
    assert "blocked" in conflict.message
    assert conflict.names == {PurePosixPath("reports/out.json")}


def test_landing_spot_that_is_existing_directory_conflicts(tmp_path: Path) -> None:
    """#774 对抗复审 P3：落点自身当前是真实目录（前代次产出
    ``reports/out.json``、本节点声明文件 ``reports``）在预检即判死——
    确定性形状冲突不应走到 S3 备份/copy/恢复补偿之后才失败；闸内文件守
    卫的目录拒绝只是无锁预检盖不住竞态时的兜底。symlink 形态例外：替换
    symlink 本体对文件守卫可逆，不判死。"""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "out.json").write_bytes(b"prior")
    (tmp_path / "linked").symlink_to(tmp_path / "reports")

    conflict = _conflict(tmp_path, [_move(tmp_path, "reports")], (), job_dir=tmp_path)

    assert conflict is not None
    assert "existing directory" in conflict.message
    assert conflict.names == {PurePosixPath("reports")}
    # 指向目录的 symlink 不是真实目录：文件守卫按 symlink 本体可逆处理。
    assert _conflict(tmp_path, [_move(tmp_path, "linked")], (), job_dir=tmp_path) is None


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
    assert "blocked" in conflict.message


def test_gate_safe_staged_moves_filters_only_blocked(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "reports").write_bytes(b"leftover")
    blocked = _move(job_dir, "reports/out.json")
    log_move = (tmp_path / "logs" / "node_a.log", job_dir / ".staging" / "node.log")

    assert gate_safe_staged_moves([blocked, log_move]) == [log_move]


def test_gate_safe_staged_moves_drops_conflicting_landings(tmp_path: Path) -> None:
    """codex #774 P2：同 source 双 move 病态声明下，失败 finish 只保留不参
    与冲突的观测 move——冲突的输出 move（target 在 job_dir）被摘除，其
    staging source 留给 node.log 观测 move 真正落盘；若保留两者，第一个
    move 消耗源、第二个被误当事务重放跳过，失败结果污染 job_dir 且日志
    仍是旧内容。"""
    job_dir = tmp_path / "job"
    output_move = (job_dir / "node.log", tmp_path / "view" / "node.log")
    log_move = _log_move(tmp_path)
    conflict = _conflict(tmp_path, [output_move, log_move], (), job_dir=job_dir)
    assert conflict is not None

    safe = gate_safe_staged_moves(
        [output_move, log_move], job_dir=job_dir, excluding=conflict.names
    )

    assert safe == [log_move]


def test_gate_safe_staged_moves_excluding_keeps_unrelated_moves(tmp_path: Path) -> None:
    """前缀冲突只摘除参与双方，未参与的产物/观测 moves 照常保留。"""
    job_dir = tmp_path / "job"
    clash_file = _move(job_dir, "reports")
    clash_nested = _move(job_dir, "reports/out.json")
    bystander = _move(job_dir, "summary.json")
    conflict = _conflict(tmp_path, [clash_file, clash_nested, bystander], (), job_dir=job_dir)
    assert conflict is not None

    safe = gate_safe_staged_moves(
        [clash_file, clash_nested, bystander], job_dir=job_dir, excluding=conflict.names
    )

    assert safe == [bystander]


def test_blocking_ancestor_stops_at_first_existing_entry(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    assert blocking_ancestor(tmp_path / "a" / "b" / "c") is None


def test_disjoint_conflicts_collect_all_names(tmp_path: Path) -> None:
    """#774 对抗复审 P2：names 是全集而非第一对——两对不相交前缀冲突的
    四个落点名都进 names（失败 finish 才能全部摘除）；message 保留第一
    处目击供人读定位。"""
    conflict = _conflict(
        tmp_path,
        [_move(tmp_path, "a"), _move(tmp_path, "a/b"), _move(tmp_path, "x")],
        ("x/y",),
        job_dir=tmp_path,
    )

    assert conflict is not None
    assert "conflicting output paths" in conflict.message
    assert conflict.names == {
        PurePosixPath("a"),
        PurePosixPath("a/b"),
        PurePosixPath("x"),
        PurePosixPath("x/y"),
    }


def test_same_prefix_siblings_all_collected(tmp_path: Path) -> None:
    """同前缀兄弟落点全收集：只带回第一对会让 reports/b.json 漏摘。"""
    conflict = _conflict(
        tmp_path,
        [_move(tmp_path, "reports"), _move(tmp_path, "reports/a.json")],
        ("reports/b.json",),
        job_dir=tmp_path,
    )

    assert conflict is not None
    assert conflict.names == {
        PurePosixPath("reports"),
        PurePosixPath("reports/a.json"),
        PurePosixPath("reports/b.json"),
    }


def test_multiple_reserved_member_clashes_all_collected(tmp_path: Path) -> None:
    """保留源冲突族同例：node.log 下的两个落点名都进 names。"""
    conflict = _conflict(tmp_path, [_log_move(tmp_path)], ("node.log/x", "node.log/y"))

    assert conflict is not None
    assert conflict.names == {PurePosixPath("node.log/x"), PurePosixPath("node.log/y")}


def test_mixed_conflict_kinds_collect_union(tmp_path: Path) -> None:
    """前缀冲突 + 现场挡位混合：两类 names 并集。"""
    (tmp_path / "blocked").write_bytes(b"leftover")
    conflict = _conflict(
        tmp_path,
        [_move(tmp_path, "a"), _move(tmp_path, "a/b"), _move(tmp_path, "blocked/x.json")],
        (),
        job_dir=tmp_path,
    )

    assert conflict is not None
    assert conflict.names == {
        PurePosixPath("a"),
        PurePosixPath("a/b"),
        PurePosixPath("blocked/x.json"),
    }
