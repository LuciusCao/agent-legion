"""completion_staged 读视图的链接语义（#759 对抗复审 P2 族、#779 终审 P1）。

链接一律覆盖：同名归档暂存字节让位 ref 字节（#759 对抗复审 N2——gated
promote 的 os.replace 可能已把 job_dir 文件换成新 inode，视图必须跟踪
本次尝试的字节）；「job_dir 残留不进视图」由调用方保证（只传本次 ref
校验提升的名），端到端回归在 tests/db/test_completion_generation_gates.py
的 test_completion_view_never_backfills_unreported_outputs_from_job_dir。
P2 族：归档成员不可信，视图是私有 scratch——链接对垃圾形状（同名目录、
文件祖先、symlink）全域清挡位，源消失的 TOCTOU 按未产出跳过，不炸异常。

节点声明 inputs 不链入本视图：#828/#830/#833 的名单裁决（名归一化 +
产物同名排除）与字节来源（dispatch 冻结 CAS 优先、job_dir 回落）已下
沉进声明校验视图族，单测钉在
tests/workflows/test_output_validation_view.py，端到端钉在
tests/db/test_completion_view_inputs.py。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent_control.completion_view import link_into_view as _link_into_view

pytestmark = pytest.mark.no_db


def test_link_overwrites_previous_attempt_bytes(tmp_path: Path) -> None:
    """覆盖语义（#759 对抗复审 N2）：gated promote 把 job_dir 文件
    os.replace 成新 inode 后，重链必须让视图跟上本次尝试的字节，而不是
    冻结在旧 inode。"""
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

    _link_into_view(("out.json",), job_dir, view_dir)

    assert (view_dir / "out.json").read_bytes() == b"attempt-2"


def test_link_overwrites_staged_archive_bytes(tmp_path: Path) -> None:
    """同名双通道（#759 对抗复审 N2）：ref 字节覆盖归档在同名位置暂存的
    字节。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    view_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"ref-bytes")
    (view_dir / "out.json").write_bytes(b"archive-staged")

    _link_into_view(("out.json",), job_dir, view_dir)

    assert (view_dir / "out.json").read_bytes() == b"ref-bytes"


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


def test_link_clears_junk_directory_at_spot(tmp_path: Path) -> None:
    """codex #774 P2 回归：归档在同名位置解出了目录——链接整棵清掉垃圾
    目录再落 ref 字节（预检的前缀互斥已保证目录内无暂存源），不再
    unlink 目录炸出 IsADirectoryError。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    (view_dir / "out.json").mkdir(parents=True)
    (view_dir / "out.json" / "junk.txt").write_bytes(b"junk")
    (job_dir / "out.json").write_bytes(b"ref-bytes")

    _link_into_view(("out.json",), job_dir, view_dir)

    assert (view_dir / "out.json").is_file()
    assert (view_dir / "out.json").read_bytes() == b"ref-bytes"


def test_link_clears_junk_file_ancestor(tmp_path: Path) -> None:
    """祖先链上的垃圾文件（reports 是文件，remote 落点是 reports/out.json）
    直接 unlink 再 mkdir——文件祖先之下不可能存在暂存源（同一 staging 目
    录里两种形状物理互斥）。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    (job_dir / "reports").mkdir(parents=True)
    (job_dir / "reports" / "out.json").write_bytes(b"ref-bytes")
    view_dir.mkdir()
    (view_dir / "reports").write_bytes(b"junk-file")

    _link_into_view(("reports/out.json",), job_dir, view_dir)

    assert (view_dir / "reports" / "out.json").read_bytes() == b"ref-bytes"


def test_link_unlinks_symlink_spot(tmp_path: Path) -> None:
    """symlink 走 unlink 而不是 rmtree（防御面；解包实际禁止链接成员）。"""
    job_dir = tmp_path / "job"
    view_dir = tmp_path / "view"
    job_dir.mkdir()
    view_dir.mkdir()
    (job_dir / "out.json").write_bytes(b"ref-bytes")
    (view_dir / "out.json").symlink_to(tmp_path / "nowhere")

    _link_into_view(("out.json",), job_dir, view_dir)

    assert not (view_dir / "out.json").is_symlink()
    assert (view_dir / "out.json").read_bytes() == b"ref-bytes"


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
    _link_into_view(("out.json",), job_dir, view_dir)

    assert not (view_dir / "out.json").exists()
