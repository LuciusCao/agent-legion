#!/usr/bin/env python3
"""Validate Worker release pins with installer behavior and structured YAML.

Installer defaults are checked in an isolated HOME/install directory with version
overrides removed and a timeout. The supported ``--help`` path proves displayed
defaults, while a normal-entry first-mkdir probe proves final effective Worker,
velites, and COMPOSE_REF before any real file, download, or Docker side effect.
Canonical declaration, effective derivation, and usage printf remain exact
single-point contracts. The command stubs are a test boundary, not a full install
or a defense against malicious branches or arbitrary dynamic code.

Compose files are parsed with ``yaml.compose(..., Loader=yaml.SafeLoader)``.
Mapping paths, duplicate fields, scalar tags/styles, and complete image values
are validated structurally; no objects are constructed and ``!reset`` needs no
custom constructor. Worker image tags use ``check_versions.normalize()`` after
Docker-compatible lexical validation, including prerelease and ``-rN`` handling.
Velites versions compare exactly. All checks are offline and idempotent, but the
installer check does execute the supported read-only help path.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from scripts.check_versions import manifest_version_from_text, normalize  # noqa: E402

WORKER_IMAGE = "ghcr.io/luciuscao/agent-legion-worker"
_TAG = r"[0-9A-Za-z._-]+"
_IMAGE_REFIX = re.compile(rf"^(?P<core>{_TAG})-r\d+$")
_STANDALONE_PATTERN = rf"\$\{{AGENT_WORKER_IMAGE:-{re.escape(WORKER_IMAGE)}:(?P<version>{_TAG})\}}"
_FIXED_PATTERN = rf"{re.escape(WORKER_IMAGE)}:(?P<version>{_TAG})"
INSTALLERS = {
    "WORKER_VERSION_DEFAULT": "printf '  --version TAG         worker 镜像 tag（默认 %s；须存在 worker-v<TAG> 发布 tag）\\n' \"$WORKER_VERSION_DEFAULT\"",
    "VELITES_VERSION_DEFAULT": "printf '  --velites-version VER velites 二进制版本（默认 %s）\\n' \"$VELITES_VERSION_DEFAULT\"",
}
ENVIRONMENTS = {"agent-legion": "AGENT_WORKER_VERSION", "velites": "VELITES_VERSION"}
_PROBE_SCRIPT = r'''docker() { if [ "${1:-} ${2:-} ${3:-}" = "compose version --short" ]; then printf '%s\n' 'v2.29.0'; else printf '%s\n' 'Docker Compose version v2.29.0'; fi; }; curl() { exit 97; }; grep() { return 0; }; uname() { printf '%s\n' 'x86_64'; }; hostname() { printf '%s\n' 'probe'; }; mkdir() { printf 'PROBE WORKER_VERSION=%s VELITES_VERSION=%s COMPOSE_REF=%s\n' "$WORKER_VERSION" "$VELITES_VERSION" "$COMPOSE_REF"; exit 0; }; script="$1"; shift; /bin/sh "$script" --help || exit $?; . "$script"'''


def _pin_matches(found: str, want: str, compare: str) -> bool:
    if compare == "exact":
        return found == want
    refixed = _IMAGE_REFIX.match(found)
    return normalize(refixed.group("core") if refixed else found) == normalize(want)


def manifest_versions(root: Path) -> dict[str, str]:
    agent = manifest_version_from_text(
        Path("pyproject.toml"), (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    velites = manifest_version_from_text(
        Path("velites/Cargo.toml"), (root / "velites/Cargo.toml").read_text(encoding="utf-8")
    )
    return {"agent-legion": agent or "", "velites": velites or ""}


def installer_errors(root: Path, expected: dict[str, str]) -> list[str]:
    path = (root / "scripts/install-worker.sh").resolve()
    errors = []
    text = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.replace("\\" + "\n", "").splitlines()]
    with tempfile.TemporaryDirectory(prefix="release-pins-") as temporary:
        options = {"capture_output": True, "text": True, "timeout": 10, "check": False}
        try:
            result = subprocess.run(
                ["/bin/sh", "-c", _PROBE_SCRIPT, "release-pin-probe", str(path)],
                cwd=temporary,
                env={"HOME": temporary, "AGENT_WORKER_INSTALL_DIR": f"{temporary}/worker-install"},
                **options,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [*errors, f"install-worker --help 执行失败或超时：{exc}"]
        for variable, usage in INSTALLERS.items():
            component = "agent-legion" if variable == "WORKER_VERSION_DEFAULT" else "velites"
            compare = "exact" if variable == "VELITES_VERSION_DEFAULT" else "image_tag"
            environment = ENVIRONMENTS[component]
            effective = f'{variable.removesuffix("_DEFAULT")}="${{{environment}:-${variable}}}"'
            marker = usage.split("（默认", 1)[0].split("'", 1)[1].strip()
            declaration = re.compile(rf'{variable}="(?P<version>{_TAG})"')
            versions = [line.split('"')[1] for line in lines if declaration.fullmatch(line)]
            if (
                len(versions) != 1
                or any(lines.count(purpose) != 1 for purpose in (effective, usage))
                or not _pin_matches(versions[0] if versions else "", expected[component], compare)
            ):
                errors.append(f"{variable}: 单点声明/用途或发布版本不符合契约")
            defaults = re.findall(rf"{re.escape(marker)}.*默认 ([^；）\s]*)", result.stdout)
            if result.returncode == 0 and (
                len(defaults) != 1 or not _pin_matches(defaults[0], expected[component], compare)
            ):
                errors.append(f"{variable}: --help 实际默认版本不符合发布线")
        probe = re.findall(
            r"PROBE WORKER_VERSION=(\S*) VELITES_VERSION=(\S*) COMPOSE_REF=(\S+)", result.stdout
        )
        if (
            result.returncode != 0
            or (Path(temporary) / "worker-install").exists()
            or len(probe) != 1
            or not _pin_matches(probe[0][0], expected["agent-legion"], "image_tag")
            or not _pin_matches(probe[0][1], expected["velites"], "exact")
            or probe[0][2] != f"worker-v{probe[0][0]}"
        ):
            errors.append(
                "WORKER_VERSION_DEFAULT / VELITES_VERSION_DEFAULT: --help/normal-path 执行失败、安装目录创建或 effective/COMPOSE_REF 不一致"
            )
    return errors


def _field(node: Node | None, key: str, path: str) -> Node:
    if not isinstance(node, MappingNode):
        raise ValueError(f"{path} 应为 mapping")
    entries = [value for name, value in node.value if getattr(name, "value", None) == key]
    if len(entries) != 1:
        raise ValueError(f"{path}.{key} 出现 {len(entries)} 次（应为 1）")
    return entries[0]


def compose_errors(root: Path, want: str) -> list[str]:
    errors = []
    for name in ("standalone", "pull.example"):
        try:
            document = yaml.compose(
                (root / f"deploy/compose.worker.{name}.yaml").read_text(encoding="utf-8"),
                yaml.SafeLoader,
            )
            worker = _field(_field(document, "services", "root"), "worker", "services")
            image = _field(worker, "image", "services.worker")
            environment = _field(worker, "environment", "services.worker")
            env = _field(environment, "AGENT_WORKER_IMAGE_VERSION", "services.worker.environment")
            pattern = _STANDALONE_PATTERN if name == "standalone" else _FIXED_PATTERN
            for field, node in (("image", image), ("AGENT_WORKER_IMAGE_VERSION", env)):
                if (
                    not isinstance(node, ScalarNode)
                    or node.tag != "tag:yaml.org,2002:str"
                    or node.style is not None
                    or not (match := re.fullmatch(pattern, node.value))
                    or not _pin_matches(match.group("version"), want, "image_tag")
                ):
                    errors.append(f"{name}.{field}: 镜像默认值与发布线 {want} 不一致或形态非法")
        except (OSError, yaml.YAMLError, ValueError) as exc:
            errors.append(f"{name}: YAML 结构读取失败：{exc}")
    return errors


def pin_errors(root: Path) -> tuple[list[str], list[str]]:
    try:
        expected = manifest_versions(root)
        errors = installer_errors(root, expected)
    except (OSError, ValueError) as exc:
        return [f"版本清单读取失败：{exc}（钉点校验无从对齐）"], []
    errors.extend(compose_errors(root, expected["agent-legion"]))
    return errors, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=project_root, help="仓库根目录")
    args = parser.parse_args(argv)
    errors, _ = pin_errors(args.root)
    if errors:
        print("\n".join(f"[release-pins] 错误: {error}" for error in errors), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
