#!/usr/bin/env python3
"""发布钉点门禁：消费者侧默认版本必须钉在当前发布线上。

``scripts/check_versions.py`` 治理清单（manifest ↔ lock 一致、组件解耦
纪律），但发布产物还有一批「消费者侧钉点」——一键安装脚本的默认版本、
独立部署 compose 的 GHCR 镜像默认 tag。这些钉点不指向任何清单，只靠
发布时人肉记忆同步，0.7.0 发布线再次漂移（install-worker.sh 停在
0.6.1/0.5.0，compose 停在 0.6.0，见 issue #504）后由 PR #503 codex
review 提出才被发现。本检查把同步纪律变成门禁：

    agent-legion   pyproject.toml   → install-worker.sh WORKER_VERSION 默认
                                      → compose.worker.standalone.yaml 镜像
                                      → compose.worker.pull.example.yaml 镜像
    velites        velites/Cargo.toml → install-worker.sh VELITES_VERSION 默认

比较复用 check_versions 的 normalize（PEP 440 ``0.8.0a0`` 与 tag 形
``0.8.0-alpha`` 归一后等价）。钉点缺失 / 正则失配一律 fail-closed 报错：
这些文件的形态就是契约，改形态必须连门禁一起改，不允许静默失明。

不做远端 tag 存在性校验：静态门保持离线、幂等；tag 未发布时安装器自身
的 manifest unknown 报错与 ``--version`` 逃生口已覆盖该窗口。
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from scripts.check_versions import normalize

project_root = Path(__file__).resolve().parents[1]

WORKER_IMAGE = "ghcr.io/luciuscao/agent-legion-worker"

# install-worker.sh 的默认版本变量行：`VAR_DEFAULT="x.y.z"` 单点定义，
# 生效值经 `${ENV:-$VAR_DEFAULT}` 派生（usage 文案同源打印）。
_INSTALLER_VAR = r'^{var}_DEFAULT="(?P<version>[0-9A-Za-z._-]+)"$'


@dataclass(frozen=True)
class Pin:
    """一个消费者侧钉点：目标文件 + 提取版本的正则（带 version 组）。"""

    label: str
    path: Path  # 相对仓库根
    pattern: re.Pattern[str]
    # 追加说明钉点语义，进错误提示（钉点不止一处时定位用）。
    where: str


def _installer_pins() -> tuple[Pin, ...]:
    installer = Path("scripts/install-worker.sh")
    return (
        Pin(
            label="install-worker WORKER_VERSION",
            path=installer,
            pattern=re.compile(_INSTALLER_VAR.format(var="WORKER_VERSION"), re.M),
            where="WORKER_VERSION_DEFAULT 默认值（镜像 tag）",
        ),
        Pin(
            label="install-worker VELITES_VERSION",
            path=installer,
            pattern=re.compile(_INSTALLER_VAR.format(var="VELITES_VERSION"), re.M),
            where="VELITES_VERSION_DEFAULT 默认值（velites 二进制版本）",
        ),
    )


def _compose_pins() -> tuple[Pin, ...]:
    pattern = re.compile(
        r"^\s*(?:image:|AGENT_WORKER_IMAGE_VERSION:)\s*"
        r"(?:\$\{AGENT_WORKER_IMAGE:-)?"
        rf"(?P<version>{re.escape(WORKER_IMAGE)}:[^\s}}]+)",
        re.M,
    )
    return tuple(
        Pin(
            label=f"{name} 镜像 tag",
            path=Path(path),
            pattern=pattern,
            where="GHCR 镜像默认 tag",
        )
        for name, path in (
            ("compose.worker.standalone.yaml", "deploy/compose.worker.standalone.yaml"),
            ("compose.worker.pull.example.yaml", "deploy/compose.worker.pull.example.yaml"),
        )
    )


PINS: tuple[Pin, ...] = _installer_pins() + _compose_pins()


def manifest_versions(root: Path) -> dict[str, str]:
    """读两个基准清单的版本；缺文件/缺版本直接按调用方 fail-closed。"""
    versions: dict[str, str] = {}
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    versions["agent-legion"] = pyproject.get("project", {}).get("version", "")
    velites_toml = tomllib.loads((root / "velites/Cargo.toml").read_text(encoding="utf-8"))
    versions["velites"] = velites_toml.get("package", {}).get("version", "")
    return versions


def pin_errors(root: Path) -> tuple[list[str], list[str]]:
    """校验每个钉点与基准清单一致；钉点缺失 / 读取失败按错误计。"""
    errors: list[str] = []
    notes: list[str] = []
    expected: dict[str, str] = {}
    try:
        expected = manifest_versions(root)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return ([f"版本清单读取失败：{exc}（钉点校验无从对齐）"], [])

    # 钉点 → 基准组件映射：installer 的 WORKER / compose 镜像对齐仓库版本；
    # VELITES 对齐 velites crate 版本。
    expected_by_label = {
        "install-worker WORKER_VERSION": expected.get("agent-legion", ""),
        "install-worker VELITES_VERSION": expected.get("velites", ""),
        "compose.worker.standalone.yaml 镜像 tag": expected.get("agent-legion", ""),
        "compose.worker.pull.example.yaml 镜像 tag": expected.get("agent-legion", ""),
    }

    for pin in PINS:
        want = expected_by_label[pin.label]
        if not want:
            errors.append(f"{pin.label}: 基准版本缺失（{pin.path} 的 {pin.where} 无从对齐）")
            continue
        path = root / pin.path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{pin.label}: 读取 {pin.path} 失败：{exc}")
            continue
        matches = list(pin.pattern.finditer(text))
        if not matches:
            errors.append(
                f"{pin.label}: 在 {pin.path} 找不到 {pin.where} 钉点——"
                "钉点形态即契约，改形态必须同步更新 scripts/check_release_pins.py"
            )
            continue
        stale = [
            match.group("version").removeprefix(f"{WORKER_IMAGE}:")
            for match in matches
            if normalize(match.group("version").removeprefix(f"{WORKER_IMAGE}:")) != normalize(want)
        ]
        if stale:
            errors.extend(
                f"{pin.label}: {pin.path} 的 {pin.where} 为 {found}，"
                f"与发布线 {want} 不一致——发布时必须同步 bump"
                f"（issue #504；修复：把 {pin.path} 内的钉点改为 {want}）"
                for found in stale
            )
        else:
            notes.append(f"{pin.label}: {want} ✓")
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    root = project_root
    if argv:
        import argparse

        parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        parser.add_argument("--root", type=Path, default=project_root, help="仓库根目录")
        args = parser.parse_args(argv)
        root = args.root

    errors, notes = pin_errors(root)
    for note in notes:
        print(f"[release-pins] {note}")
    for error in errors:
        print(f"[release-pins] 错误: {error}", file=sys.stderr)
    if errors:
        print(f"[release-pins] 发布钉点检查失败（{len(errors)} 处）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
