"""completion_staged 读视图的链接语义（#759 对抗复审 P1、P2 族）。

第一遍链接（expected 全集）不覆盖归档已暂存的字节；第二遍链接（remote
名）必须覆盖——gated promote 可能已把 job_dir 里同代次重跑的残留文件
os.replace 成新 inode，读视图若冻结在旧 inode，校验/分片读就会消费
「上一次尝试」的字节。P2 族：归档成员不可信，视图是私有 scratch——
链接对垃圾形状（同名目录、文件祖先、symlink）全域，overwrite 遍清挡
位垃圾，第一遍遇挡位跳过（按未产出判 missing），都不再炸异常。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent_control.completion_view import link_into_view as _link_into_view

pytestmark = pytest.mark.no_db


def test_second_pass_overwrites_previous_attempt_bytes(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    view_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"attempt-1")
    _link_into_view(("out.json",), job_dir, view_dir)
    assert (view_dir / "out.json").read_bytes() == b"attempt-1"
    # gated promote 的 os.replace 语义：换目录项到新 inode。
    (job_dir / "out.json").unlink()
    (job_dir / "out.json").write_bytes(b"attempt-2")

    _link_into_view(("out.json",), job_dir, view_dir, overwrite=True)

    assert (view_dir / "out.json").read_bytes() == b"attempt-2"


def test_first_pass_never_overwrites_staged_archive_bytes(tmp_path: Path) -> None:
    """对照：不带 overwrite 的第一遍保留归档暂存字节（本次尝试的归档产物
    优先于 job_dir 里的同名残留）。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    view_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"job-dir-leftover")
    (view_dir / "out.json").write_bytes(b"archive-staged")

    _link_into_view(("out.json",), job_dir, view_dir)

    assert (view_dir / "out.json").read_bytes() == b"archive-staged"


def test_link_falls_back_to_copy_when_hardlink_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不支持硬链接的挂载（P3 部署边缘）：退化为同内容拷贝，视图语义不变。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    view_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"payload")

    def _no_hardlink(source: Path, target: Path) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr("server.app.agent_control.completion_view.os.link", _no_hardlink)
    _link_into_view(("out.json",), job_dir, view_dir)

    assert (view_dir / "out.json").read_bytes() == b"payload"


def test_overwrite_pass_clears_junk_directory_at_spot(tmp_path: Path) -> None:
    """codex #774 P2 回归：归档在同名位置解出了目录——overwrite 遍整棵清
    掉垃圾目录再链接 ref 字节（预检的前缀互斥已保证目录内无暂存源），不
    再 unlink 目录炸出 IsADirectoryError。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    (view_dir / "out.json").mkdir(parents=True)
    (view_dir / "out.json" / "junk.txt").write_bytes(b"junk")
    (job_dir / "out.json").write_bytes(b"ref-bytes")

    _link_into_view(("out.json",), job_dir, view_dir, overwrite=True)

    assert (view_dir / "out.json").is_file()
    assert (view_dir / "out.json").read_bytes() == b"ref-bytes"


def test_overwrite_pass_clears_junk_file_ancestor(tmp_path: Path) -> None:
    """祖先链上的垃圾文件（reports 是文件，remote 落点是 reports/out.json）
    直接 unlink 再 mkdir——文件祖先之下不可能存在暂存源（同一 staging 目
    录里两种形状物理互斥）。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    (job_dir / "reports").mkdir(parents=True)
    (job_dir / "reports" / "out.json").write_bytes(b"ref-bytes")
    view_dir.mkdir()
    (view_dir / "reports").write_bytes(b"junk-file")

    _link_into_view(("reports/out.json",), job_dir, view_dir, overwrite=True)

    assert (view_dir / "reports" / "out.json").read_bytes() == b"ref-bytes"


def test_overwrite_pass_unlinks_symlink_spot(tmp_path: Path) -> None:
    """symlink 走 unlink 而不是 rmtree（防御面；解包实际禁止链接成员）。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    view_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"ref-bytes")
    (view_dir / "out.json").symlink_to(tmp_path / "nowhere")

    _link_into_view(("out.json",), job_dir, view_dir, overwrite=True)

    assert not (view_dir / "out.json").is_symlink()
    assert (view_dir / "out.json").read_bytes() == b"ref-bytes"


def test_first_pass_skips_when_junk_file_blocks_ancestor(tmp_path: Path) -> None:
    """不带 overwrite 的第一遍：垃圾文件遮住祖先 → 跳过（该名按未产出判
    missing），不炸 mkdir。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    (job_dir / "reports").mkdir(parents=True)
    (job_dir / "reports" / "out.json").write_bytes(b"leftover")
    view_dir.mkdir()
    (view_dir / "reports").write_bytes(b"junk-file")

    _link_into_view(("reports/out.json",), job_dir, view_dir)

    assert (view_dir / "reports").read_bytes() == b"junk-file"  # 视图原样


def test_first_pass_keeps_directory_at_spot(tmp_path: Path) -> None:
    """第一遍遇同名目录同样跳过保留——produced 判定视图为非文件即 missing。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"leftover")
    (view_dir / "out.json").mkdir(parents=True)

    _link_into_view(("out.json",), job_dir, view_dir)

    assert (view_dir / "out.json").is_dir()


def test_link_skips_when_source_vanishes_mid_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """源侧 TOCTOU（#759 对抗复审 P2-A）：is_file→link 窗口内源被并发
    promote 的备份步 rename 走，link 与 copy 接连 FileNotFoundError——
    按「该名未产出」跳过（produced 判 missing），不炸穿结果提交。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    view_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"payload")

    def _gone(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr("server.app.agent_control.completion_view.os.link", _gone)
    monkeypatch.setattr("server.app.agent_control.completion_view.shutil.copy2", _gone)
    _link_into_view(("out.json",), job_dir, view_dir, overwrite=True)

    assert not (view_dir / "out.json").exists()
