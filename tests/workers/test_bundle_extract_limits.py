"""#967：Worker bundle 解压的成员数 / 解压总量上限（worker/bundle_io.py）。

safe_extract_tree 是 Worker 侧唯一的不可信归档解压入口（agent 与 code
两条执行准备路径共用）；超限必须在 extractall 写出任何字节之前拒绝，且以
ValueError 族抛出——执行准备的遏制边界把它转为一次显式 failed 上报。
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from worker import bundle_io
from worker.bundle_io import BundleLimitExceeded, safe_extract_tree

pytestmark = pytest.mark.no_db


def _bundle(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_normal_bundle_extracts(tmp_path: Path) -> None:
    archive = _bundle(tmp_path / "b.tar.gz", {"manifest.json": b"{}", "skill/SKILL.md": b"# s"})
    safe_extract_tree(archive, tmp_path / "out")
    assert (tmp_path / "out" / "skill" / "SKILL.md").read_bytes() == b"# s"


def test_member_count_over_ceiling_is_rejected_before_extracting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bundle_io, "MAX_BUNDLE_MEMBERS", 3)
    archive = _bundle(tmp_path / "b.tar.gz", {f"f{i}.txt": b"x" for i in range(4)})
    with pytest.raises(BundleLimitExceeded, match="more than 3 members"):
        safe_extract_tree(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_unpacked_size_over_ceiling_is_rejected_before_extracting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """高压缩比成员（全零大文件）：按头部声明 size 累加，越线即拒——不落盘。"""
    monkeypatch.setattr(bundle_io, "MAX_BUNDLE_UNPACKED_BYTES", 1024 * 1024)
    archive = _bundle(
        tmp_path / "b.tar.gz", {"a.bin": b"\0" * (600 * 1024), "b.bin": b"\0" * (600 * 1024)}
    )
    assert archive.stat().st_size < 64 * 1024  # 压缩体积远小于解压总量
    with pytest.raises(BundleLimitExceeded, match="unpacks to more than 1048576 bytes"):
        safe_extract_tree(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_limit_error_is_a_value_error() -> None:
    """与 unsafe member 拒绝同族：执行准备的遏制边界据此转 failed 上报。"""
    assert issubclass(BundleLimitExceeded, ValueError)


def test_defaults_are_bounded() -> None:
    assert bundle_io.MAX_BUNDLE_MEMBERS == 20_000
    assert bundle_io.MAX_BUNDLE_UNPACKED_BYTES == 1024 * 1024 * 1024
