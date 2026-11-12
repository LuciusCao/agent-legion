#!/usr/bin/env python3
"""Keep Worker installer and Compose defaults aligned with release manifests.

The Worker image tags use ``check_versions.normalize()`` so PEP 440 and tag-style
prereleases compare equally; an ``-rN`` image rebuild suffix is also accepted.
Velites tags instead match its Cargo manifest exactly because the release workflow
uses that literal version in the tag name. Installer uses an exact-name occurrence
census plus a three-purpose allowlist: one canonical declaration, one effective
derivation, and one usage printf must each appear exactly once and explain all
three occurrences. Quoted, escaped, commented, prose, or eval-text references and
changed allowed lines fail closed; new legitimate references require updating this
contract. Compose fields are counted and their complete values must match the
standalone interpolation or fixed-scalar contract. Installer text is normalized only by POSIX
backslash-newline removal first; a unique continued assignment is accepted when
its normalized shape is valid. Compose tags must fullmatch the Docker-compatible
character set before version normalization, so PEP 440 local suffixes are not
accepted as Docker tags. Missing, duplicate, or malformed pins are errors.

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
_TAG = r"[0-9A-Za-z._-]+"
_IMAGE_REFIX = re.compile(rf"^(?P<core>{_TAG})-r\d+$")
_VALUE_TAIL = r"(?:[ \t]*|[ \t]+#.*)"
_STANDALONE_VALUE = re.compile(
    rf"[ \t]*\$\{{AGENT_WORKER_IMAGE:-{re.escape(WORKER_IMAGE)}:"
    rf"(?P<version>{_TAG})\}}{_VALUE_TAIL}"
)
_FIXED_VALUE = re.compile(rf"[ \t]*{re.escape(WORKER_IMAGE)}:(?P<version>{_TAG}){_VALUE_TAIL}")


@dataclass(frozen=True)
class Pin:
    label: str
    path: Path
    component: str
    discovery: re.Pattern[str]
    shape: re.Pattern[str]
    where: str
    compare: str = "image_tag"
    purposes: tuple[str, ...] = ()


def _installer_pins() -> tuple[Pin, ...]:
    installer = Path("scripts/install-worker.sh")
    return tuple(
        Pin(
            label=f"install-worker {variable.removesuffix('_DEFAULT')}",
            path=installer,
            component="agent-legion" if variable == "WORKER_VERSION_DEFAULT" else "velites",
            discovery=re.compile(rf"(?<![0-9A-Za-z_]){variable}(?![0-9A-Za-z_])"),
            shape=re.compile(rf'^[ \t]*{variable}="(?P<version>{_TAG})"[ \t]*$', re.M),
            where=f"{variable} 默认值",
            compare="exact" if variable == "VELITES_VERSION_DEFAULT" else "image_tag",
            purposes=(
                'WORKER_VERSION="${AGENT_WORKER_VERSION:-$WORKER_VERSION_DEFAULT}"'
                if variable == "WORKER_VERSION_DEFAULT"
                else 'VELITES_VERSION="${VELITES_VERSION:-$VELITES_VERSION_DEFAULT}"',
                "printf '  --version TAG         worker 镜像 tag（默认 %s；须存在 worker-v<TAG> 发布 tag）\\n' \"$WORKER_VERSION_DEFAULT\""
                if variable == "WORKER_VERSION_DEFAULT"
                else "printf '  --velites-version VER velites 二进制版本（默认 %s）\\n' \"$VELITES_VERSION_DEFAULT\"",
            ),
        )
        for variable in ("WORKER_VERSION_DEFAULT", "VELITES_VERSION_DEFAULT")
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
        text = text.replace("\\" + "\n", "") if pin.purposes else text
        matches = list(pin.discovery.finditer(text))
        if len(matches) != (3 if pin.purposes else 1):
            found = max(0, len(matches) - len(pin.purposes)) if pin.purposes else len(matches)
            errors.append(
                f"{pin.label}: 在 {pin.path} 找到 {found} 处 {pin.where} "
                f"（变量名总出现 {len(matches)} 次，应为 {3 if pin.purposes else 1}）"
                "——钉点形态即契约，改形态必须同步更新 scripts/check_release_pins.py"
            )
            continue
        shaped = None
        if pin.purposes:
            canonical = list(pin.shape.finditer(text))
            if len(canonical) == 1 and all(
                sum(line.strip() == purpose for line in text.splitlines()) == 1
                for purpose in pin.purposes
            ):
                shaped = canonical[0]
        elif not matches[0].group("prefix") and not matches[0].groupdict().get("suffix"):
            shaped = pin.shape.fullmatch(matches[0].group("value"))
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
