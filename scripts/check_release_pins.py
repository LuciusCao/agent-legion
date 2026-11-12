#!/usr/bin/env python3
"""Keep Worker installer and Compose defaults aligned with release manifests.

The Worker image tags use ``check_versions.normalize()`` so PEP 440 and tag-style
prereleases compare equally; an ``-rN`` image rebuild suffix is also accepted.
Velites tags instead match its Cargo manifest exactly because the release workflow
uses that literal version in the tag name. Discovery and validation are separate:
every actual assignment or field is counted, including empty or differently quoted
forms, then the complete value must match its contract (standalone interpolation
versus fixed Compose scalar). Missing, duplicate, or malformed pins are errors.

This check is deliberately offline and read-only. Release-tag existence remains
an installer/runtime concern, with explicit version overrides as the escape hatch.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from scripts.check_versions import normalize  # noqa: E402

WORKER_IMAGE = "ghcr.io/luciuscao/agent-legion-worker"
_IMAGE_REFIX = re.compile(r"^(?P<core>[0-9A-Za-z._-]+)-r\d+$")
_VALUE_TAIL = r"(?:[ \t]*|[ \t]+#.*)"
_INSTALLER_VALUE = re.compile(r'"(?P<version>[0-9A-Za-z._-]+)"[ \t]*')
_STANDALONE_VALUE = re.compile(
    rf"[ \t]*\$\{{AGENT_WORKER_IMAGE:-{re.escape(WORKER_IMAGE)}:"
    rf"(?P<version>[^\s}}]+)\}}{_VALUE_TAIL}"
)
_FIXED_VALUE = re.compile(rf"[ \t]*{re.escape(WORKER_IMAGE)}:(?P<version>[^\s#]+){_VALUE_TAIL}")


@dataclass(frozen=True)
class Pin:
    label: str
    path: Path
    component: str
    discovery: re.Pattern[str]
    shape: re.Pattern[str]
    where: str
    compare: str = "image_tag"


def _installer_pins() -> tuple[Pin, ...]:
    installer = Path("scripts/install-worker.sh")
    return tuple(
        Pin(
            label=f"install-worker {variable}",
            path=installer,
            component="agent-legion" if variable == "WORKER_VERSION" else "velites",
            discovery=re.compile(
                rf"^[ \t]*(?P<prefix>(?:(?:export|readonly)[ \t]+)?){variable}_DEFAULT="
                r"(?P<value>.*)$",
                re.M,
            ),
            shape=_INSTALLER_VALUE,
            where=f"{variable}_DEFAULT 默认值",
            compare="exact" if variable == "VELITES_VERSION" else "image_tag",
        )
        for variable in ("WORKER_VERSION", "VELITES_VERSION")
    )


def _compose_pins() -> tuple[Pin, ...]:
    return tuple(
        Pin(
            label=f"compose.worker.{name}.yaml {field}",
            path=Path(f"deploy/compose.worker.{name}.yaml"),
            component="agent-legion",
            discovery=re.compile(
                rf"^[ \t]*(?P<prefix>(?:-[ \t]+)?[\"']?){field}(?P<suffix>[\"']?):"
                r"(?P<value>.*)$",
                re.M,
            ),
            shape=_STANDALONE_VALUE if name == "standalone" else _FIXED_VALUE,
            where=f"{field}: GHCR 镜像默认 tag",
        )
        for name in ("standalone", "pull.example")
        for field in ("image", "AGENT_WORKER_IMAGE_VERSION")
    )


PINS: tuple[Pin, ...] = _installer_pins() + _compose_pins()


def _pin_matches(found: str, want: str, compare: str) -> bool:
    if compare == "exact":
        return found == want
    core = found
    if compare == "image_tag":
        refixed = _IMAGE_REFIX.match(found)
        if refixed:
            core = refixed.group("core")
    return normalize(core) == normalize(want)


def _manifest_version(root: Path, rel: Path, section: str) -> str:
    data = tomllib.loads((root / rel).read_text(encoding="utf-8"))
    value = data.get(section, {}).get("version")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{rel} 缺 {section}.version")
    return value.strip()


def manifest_versions(root: Path) -> dict[str, str]:
    return {
        "agent-legion": _manifest_version(root, Path("pyproject.toml"), "project"),
        "velites": _manifest_version(root, Path("velites/Cargo.toml"), "package"),
    }


def pin_errors(root: Path) -> tuple[list[str], list[str]]:
    """Return release-pin errors and successful pin summaries for ``root``."""
    errors: list[str] = []
    notes: list[str] = []
    try:
        expected = manifest_versions(root)
    except (OSError, ValueError) as exc:
        return ([f"版本清单读取失败：{exc}（钉点校验无从对齐）"], [])

    for pin in PINS:
        want = expected[pin.component]
        try:
            text = (root / pin.path).read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{pin.label}: 读取 {pin.path} 失败：{exc}")
            continue
        matches = list(pin.discovery.finditer(text))
        if len(matches) != 1:
            errors.append(
                f"{pin.label}: 在 {pin.path} 找到 {len(matches)} 处 {pin.where} "
                "钉点（应为 1）——钉点形态即契约，改形态必须同步更新 "
                "scripts/check_release_pins.py"
            )
            continue
        match = matches[0]
        shaped = None
        if not match.group("prefix") and not match.groupdict().get("suffix"):
            shaped = pin.shape.fullmatch(match.group("value"))
        if shaped is None:
            errors.append(
                f"{pin.label}: {pin.path} 的 {pin.where} 完整赋值/字段形态非法"
                "（空值、引号、插值括号、后缀或垃圾文本不符合契约）"
            )
            continue
        found = shaped.group("version")
        if not _pin_matches(found, want, pin.compare):
            errors.append(
                f"{pin.label}: {pin.path} 的 {pin.where} 为 {found}，"
                f"与发布线 {want} 不一致——发布时必须同步 bump"
                f"（issues #504/#1112；修复：把该钉点改为 {want}）"
            )
        else:
            notes.append(f"{pin.label}: {found} ✓")
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=project_root, help="仓库根目录")
    args = parser.parse_args(argv)

    errors, notes = pin_errors(args.root)
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
