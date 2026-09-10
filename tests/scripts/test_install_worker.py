"""install-worker.sh 的发布契约守卫（codex P1 on #583 的防复发）。

两类复发事故：
1. sha256 解析漂移（#583）：发布端 velites-release.yml 用
   ``sha256sum ./*.tar.gz`` 生成校验清单，文件名带 ``./`` 前缀；安装端
   awk 精确匹配裸文件名，恒为空 → 默认升级路径中止。本测试把脚本里
   真实的 awk 管道抠出来，对「发布端同款格式」的合成清单执行，
   钉住解析契约。
2. 默认版本漂移（#529 的同类）：install-worker.sh 的 VELITES_VERSION
   默认值必须与 velites/Cargo.toml 的 package version 一致——velites
   落版 bump 是两处手工同步点，漏一边就是「安装默认装旧版」。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install-worker.sh"
CARGO_TOML = REPO_ROOT / "velites" / "Cargo.toml"

TARBALL = "velites-0.0.0-test-aarch64-apple-darwin.tar.gz"
HASH = "a" * 64


def _extract_expected_pipeline(script: str) -> str:
    """抠出脚本里 expected=$(...) 的完整命令替换体。"""
    match = re.search(r'expected="\$\((awk .+?)\)"', script)
    assert match, "install-worker.sh 里找不到 expected 的 awk 管道（结构变了就同步本测试）"
    return match.group(1)


def test_sha256_listing_parses_release_side_dot_slash_format(tmp_path: Path) -> None:
    # 发布端格式（velites-release.yml 的 sha256sum 输出）：文件名带 ./ 前缀
    (tmp_path / "sha256.txt").write_text(
        f"{HASH}  ./{TARBALL}\n{'b' * 64}  ./velites-0.0.0-test-x86_64-unknown-linux-gnu.tar.gz\n"
    )
    pipeline = _extract_expected_pipeline(INSTALLER.read_text())
    result = subprocess.run(
        ["bash", "-c", f"{pipeline}"],
        env={"VELITES_TARBALL": TARBALL, "TMPDIR_": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == HASH


def test_sha256_listing_parses_bare_filename_format(tmp_path: Path) -> None:
    # 无前缀格式（velites-release 已归一为裸 glob）同样必须命中
    (tmp_path / "sha256.txt").write_text(f"{HASH}  {TARBALL}\n")
    pipeline = _extract_expected_pipeline(INSTALLER.read_text())
    result = subprocess.run(
        ["bash", "-c", f"{pipeline}"],
        env={"VELITES_TARBALL": TARBALL, "TMPDIR_": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == HASH


def test_installer_default_version_matches_velites_crate() -> None:
    script = INSTALLER.read_text()
    default = re.search(r'VELITES_VERSION="\$\{VELITES_VERSION:-([^}]+)\}"', script)
    assert default, "install-worker.sh 里找不到 VELITES_VERSION 默认值"
    package = re.search(r'(?ms)^\[package\].*?^version = "([^"]+)"', CARGO_TOML.read_text())
    assert package, "velites/Cargo.toml 里找不到 package version"
    assert default.group(1) == package.group(1), (
        f"install-worker.sh 默认 velites 版本 {default.group(1)} 与 "
        f"velites/Cargo.toml {package.group(1)} 不一致——velites 落版时两处必须同步 bump"
    )
