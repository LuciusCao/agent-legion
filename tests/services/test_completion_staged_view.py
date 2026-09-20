"""completion_staged 读视图的链接语义（#759 对抗复审 P1）。

第一遍链接（expected 全集）不覆盖归档已暂存的字节；第二遍链接（remote
名）必须覆盖——gated promote 可能已把 job_dir 里同代次重跑的残留文件
os.replace 成新 inode，读视图若冻结在旧 inode，校验/分片读就会消费
「上一次尝试」的字节。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent_control.completion_staged import _link_into_view

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

    monkeypatch.setattr("server.app.agent_control.completion_staged.os.link", _no_hardlink)
    _link_into_view(("out.json",), job_dir, view_dir)

    assert (view_dir / "out.json").read_bytes() == b"payload"
