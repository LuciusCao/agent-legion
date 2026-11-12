#!/usr/bin/env python3
"""Keep Worker installer and Compose defaults aligned with release manifests.

The Worker image tags use ``check_versions.normalize()`` so PEP 440 and tag-style
prereleases compare equally; an ``-rN`` image rebuild suffix is also accepted.
Velites tags instead match its Cargo manifest exactly because the release workflow
uses that literal version in the tag name. Missing files, malformed manifests, and
missing or duplicate pins are errors: changing a pinned shape requires updating
this contract too.

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
_INSTALLER_VAR = r'^{var}_DEFAULT="(?P<version>[0-9A-Za-z._-]+)"$'
_IMAGE_REFIX = re.compile(r"^(?P<core>[0-9A-Za-z._-]+)-r\d+$")


@dataclass(frozen=True)
class Pin:
    label: str
    path: Path
    component: str
    pattern: re.Pattern[str]
    where: str
    compare: str = "image_tag"


def _installer_pins() -> tuple[Pin, ...]:
    installer = Path("scripts/install-worker.sh")
    return (
        Pin(
            label="install-worker WORKER_VERSION",
            path=installer,
            component="agent-legion",
            pattern=re.compile(_INSTALLER_VAR.format(var="WORKER_VERSION"), re.M),
            where="WORKER_VERSION_DEFAULT 默认值",
        ),
        Pin(
            label="install-worker VELITES_VERSION",
            path=installer,
            component="velites",
            pattern=re.compile(_INSTALLER_VAR.format(var="VELITES_VERSION"), re.M),
            where="VELITES_VERSION_DEFAULT 默认值",
            compare="exact",
        ),
    )


def _compose_pins() -> tuple[Pin, ...]:
    pins: list[Pin] = []
    for name in ("standalone", "pull.example"):
        path = Path(f"deploy/compose.worker.{name}.yaml")
        for field in ("image", "AGENT_WORKER_IMAGE_VERSION"):
            pattern = re.compile(
                rf"^\s*{field}:\s*"
                r"(?:\$\{AGENT_WORKER_IMAGE:-)?"
                rf"{re.escape(WORKER_IMAGE)}:(?P<version>[^\s}}]+)",
                re.M,
            )
            pins.append(
                Pin(
                    label=f"compose.worker.{name}.yaml {field}",
                    path=path,
                    component="agent-legion",
                    pattern=pattern,
                    where=f"{field}: GHCR 镜像默认 tag",
                )
            )
    return tuple(pins)


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
        matches = list(pin.pattern.finditer(text))
        if len(matches) != 1:
            errors.append(
                f"{pin.label}: 在 {pin.path} 找到 {len(matches)} 处 {pin.where} "
                "钉点（应为 1）——钉点形态即契约，改形态必须同步更新 "
                "scripts/check_release_pins.py"
            )
            continue
        found = matches[0].group("version")
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
