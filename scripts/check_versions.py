#!/usr/bin/env python3
"""版本清单一致性与发版解耦纪律检查。

三个发版组件各自持有独立版本线，不再随仓库发版锁步 bump：

    agent-legion  pyproject.toml（+ uv.lock）          仓库版本（host + worker + shared）
    velites       velites/Cargo.toml（+ Cargo.lock）   Rust crate
    frontend      frontend/package.json（+ lockfile）   npm 包

锁步 bump 的代价是真实的：``scripts/ensure-velites.sh`` 以 ``velites/`` 子树的
git tree hash 做二进制新鲜度指纹，Docker 的 velites-build stage 以 ``COPY
velites/`` 为缓存键——Cargo.toml 里的版本号一变（即使 velites 源码没动）就会
触发全量 ``cargo build --release`` 与镜像层重建；frontend/package.json 同理
击穿 ``npm ci`` 缓存层。

两条规则：

1. 清单 ↔ lock 一致：改版本必须同步再生成对应 lock（``uv lock`` / 编辑
   Cargo.toml 后的 lock 刷新 / ``npm install --package-lock-only``）。
2. 解耦纪律（需要 git）：velites / frontend 的版本前进必须伴随「自该组件
   版本上一次变化以来」的源码改动。锚点取版本号上一次 differing 的提交而非
   HEAD^，这样「特性先合入、落版单独提交」的正常节奏不会被误伤，而「仓库
   发版顺手把 velites 也 bump 一下」的锁步模式（两次 bump 之间 velites/
   无任何源码提交）会被拒绝。pyproject 是仓库整体版本，不适用本规则。

git 不可用（非 repo、测试 fixture）时只跑规则 1 并提示跳过；shallow clone
历史不足时锚点不可达，规则 2 静默降级（CI 为 fetch-depth: 0，不受影响）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Component:
    """一个可独立发版的组件及其版本清单/lock/源码范围。"""

    label: str
    manifest: Path  # 相对仓库根
    lock: Path
    source_prefix: str
    # 版本清单与 lock 本身不算「源码改动」——落版提交只有它们变化是正常的。
    non_source_paths: frozenset[str]


COMPONENTS: tuple[Component, ...] = (
    Component(
        label="agent-legion",
        manifest=Path("pyproject.toml"),
        lock=Path("uv.lock"),
        source_prefix="",  # 仓库整体版本：规则 2 不适用
        non_source_paths=frozenset(),
    ),
    Component(
        label="velites",
        manifest=Path("velites/Cargo.toml"),
        lock=Path("velites/Cargo.lock"),
        source_prefix="velites/",
        non_source_paths=frozenset({"velites/Cargo.toml", "velites/Cargo.lock"}),
    ),
    Component(
        label="frontend",
        manifest=Path("frontend/package.json"),
        lock=Path("frontend/package-lock.json"),
        source_prefix="frontend/",
        non_source_paths=frozenset({"frontend/package.json", "frontend/package-lock.json"}),
    ),
)

# 各 lock 的刷新命令（错误提示用）。
refresh_hint = {
    "uv.lock": "uv lock",
    "Cargo.lock": "cargo update -w",
    "package-lock.json": "npm install --package-lock-only",
}


def normalize(version: str) -> str:
    """把 PEP 440 / semver 两种写法归一到可比形式。

    ``0.4.0a0``（uv）与 ``0.4.0-alpha``（cargo/npm）指同一个预发布版本；
    归一后 ``0.4.0a0`` == ``0.4.0a0``，``0.4.0-alpha.1`` == ``0.4.0a1``。
    解析不了的串原样返回（退化为字符串比较）。
    """
    v = version.strip().lower().removeprefix("v")
    match = re.match(
        r"^(\d+(?:\.\d+)*)"  # 核心版本号
        r"(?:[-._]?(alpha|beta|rc|dev|a|b|c)"  # 预发布段（词形或字母形）
        r"(?:[-._]?(\d+))?)?"  # 预发布序号（可省略，视为 0）
        r"(?:\+.*)?$",  # build metadata 不参与比较
        v,
    )
    if not match:
        return v
    core, pre, num = match.groups()
    if pre is None:
        return core
    pre = {"alpha": "a", "beta": "b"}.get(pre, pre)
    return f"{core}{pre}{num or '0'}"


def _read_text(root: Path, rel: Path) -> str:
    return (root / rel).read_text(encoding="utf-8")


def manifest_version_from_text(rel: Path, text: str) -> str | None:
    """从清单文本解析版本号；结构不符返回 None（调用方按缺字段报错）。"""
    if rel.name == "pyproject.toml":
        version: str | None = tomllib.loads(text).get("project", {}).get("version")
        return version
    if rel.name == "Cargo.toml":
        version = tomllib.loads(text).get("package", {}).get("version")
        return version
    if rel.name == "package.json":
        version = json.loads(text).get("version")
        return version
    raise ValueError(f"unknown manifest: {rel}")


def _lock_package_version(text: str, package: str) -> str | None:
    """uv.lock / Cargo.lock 共用 [[package]] 结构。"""
    for entry in tomllib.loads(text).get("package", []):
        if entry.get("name") == package:
            found: str | None = entry.get("version")
            return found
    return None


def lock_versions(rel: Path, text: str) -> tuple[str | None, ...]:
    """返回 lock 中承载组件版本的全部字段值（package-lock 有两处）。"""
    if rel.name == "uv.lock":
        return (_lock_package_version(text, "agent-legion"),)
    if rel.name == "Cargo.lock":
        return (_lock_package_version(text, "velites"),)
    if rel.name == "package-lock.json":
        data = json.loads(text)
        return (data.get("version"), data.get("packages", {}).get("", {}).get("version"))
    raise ValueError(f"unknown lockfile: {rel}")


def lock_consistency_errors(root: Path) -> tuple[list[str], list[str]]:
    """规则 1：清单存在、版本可解析、lock 与清单一致。"""
    errors: list[str] = []
    summary: list[str] = []
    for comp in COMPONENTS:
        manifest_path = root / comp.manifest
        lock_path = root / comp.lock
        if not manifest_path.is_file():
            errors.append(f"{comp.label}: 找不到版本清单 {comp.manifest}")
            continue
        try:
            manifest_v = manifest_version_from_text(comp.manifest, _read_text(root, comp.manifest))
        except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{comp.label}: {comp.manifest} 解析失败：{exc}")
            continue
        if not manifest_v:
            errors.append(f"{comp.label}: {comp.manifest} 缺 version 字段")
            continue
        summary.append(f"{comp.label} {manifest_v}")
        if not lock_path.is_file():
            errors.append(f"{comp.label}: 找不到 lockfile {comp.lock}")
            continue
        try:
            lock_vs = lock_versions(comp.lock, _read_text(root, comp.lock))
        except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{comp.label}: {comp.lock} 解析失败：{exc}")
            continue
        errors.extend(
            f"{comp.label}: {comp.manifest} 版本 {manifest_v} 与 {comp.lock} "
            f"承载的 {lock_v} 不一致——改版本后重新生成 lock（{refresh_hint[comp.lock.name]}）"
            for lock_v in lock_vs
            if lock_v is None or normalize(lock_v) != normalize(manifest_v)
        )
    return errors, summary


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def _last_version_change(root: Path, comp: Component, current: str) -> tuple[str, str] | None:
    """定位组件版本上一次 differing 的提交，返回 (sha, 旧版本)。

    只走 touch 过该清单的提交（``git log -- <manifest>``）；清单尚不存在
    的更早历史直接停（组件刚引入，首次定版不受规则约束）。shallow 历史
    走不到 differing 版本时返回 None（规则静默降级）。
    """
    log = _git(root, "log", "--format=%H", "--", comp.manifest.as_posix())
    if log.returncode != 0:
        return None
    for sha in log.stdout.split():
        show = _git(root, "show", f"{sha}:{comp.manifest.as_posix()}")
        if show.returncode != 0:
            return None
        old = manifest_version_from_text(comp.manifest, show.stdout)
        if old is None or normalize(old) != normalize(current):
            return sha, old or "(缺失)"
    return None


def decoupling_errors(root: Path) -> tuple[list[str], list[str]]:
    """规则 2：独立组件的版本前进必须伴随锚点以来的源码改动。"""
    errors: list[str] = []
    notes: list[str] = []
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        notes.append("git 不可用或无提交（非 repo / 空 repo）：跳过解耦纪律检查")
        return errors, notes

    for comp in COMPONENTS:
        if not comp.source_prefix:
            continue  # 仓库整体版本：落版提交只有清单/CHANGELOG 变化是正常的
        manifest_path = root / comp.manifest
        if not manifest_path.is_file():
            continue  # 缺清单已在规则 1 报错
        try:
            current = manifest_version_from_text(comp.manifest, _read_text(root, comp.manifest))
        except (tomllib.TOMLDecodeError, json.JSONDecodeError):
            continue
        if not current:
            continue
        anchor = _last_version_change(root, comp, current)
        if anchor is None:
            continue  # 版本自首次出现未变过：无锁步可言
        anchor_sha, old_version = anchor
        diff = _git(root, "diff", "--name-only", anchor_sha)
        if diff.returncode != 0:
            notes.append(f"{comp.label}: git diff 失败，跳过该组件的解耦检查")
            continue
        source_changed = [
            path
            for path in diff.stdout.splitlines()
            if path.startswith(comp.source_prefix) and path not in comp.non_source_paths
        ]
        if not source_changed:
            errors.append(
                f"{comp.label}: 版本 {old_version} → {current}，但自锚点 "
                f"{anchor_sha[:12]} 以来 {comp.source_prefix} 内没有任何源码改动"
                "（仅版本清单/lock 变化）——这是仓库发版锁步 bump 的特征。"
                f"{comp.label} 版本线独立于仓库版本：仓库发版不要动 {comp.manifest}，"
                "只有组件自身有源码改动的发版才前进（规则见 "
                "scripts/check_versions.py 模块 docstring）"
            )
        else:
            notes.append(
                f"{comp.label}: {old_version} → {current}，锚点以来源码改动 "
                f"{len(source_changed)} 个文件 ✓"
            )
    return errors, notes


def check_all(root: Path) -> tuple[list[str], list[str]]:
    lock_errors, summary = lock_consistency_errors(root)
    decouple_errors, decouple_notes = decoupling_errors(root)
    notes = [f"版本清单：{' · '.join(summary)}"] if summary else []
    notes.extend(decouple_notes)
    return [*lock_errors, *decouple_errors], notes


def main(argv: list[str] | None = None) -> int:
    root = project_root
    if argv:
        import argparse

        parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        parser.add_argument("--root", type=Path, default=project_root, help="仓库根目录")
        args = parser.parse_args(argv)
        root = args.root

    errors, notes = check_all(root)
    for note in notes:
        print(f"[versions] {note}")
    for error in errors:
        print(f"[versions] 错误: {error}", file=sys.stderr)
    if errors:
        print(f"[versions] 版本检查失败（{len(errors)} 处）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
