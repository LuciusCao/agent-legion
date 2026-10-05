"""Supply-chain pinning contract (issue #969, #917 安全 P2).

契约：
- .github 下全部 workflow 的 ``uses:`` 都按 40 位 commit sha 钉死，并带
  ``# <版本>`` 注释（Dependabot 识别该写法，升级 PR 同时改 sha 与注释）；
  本地 action（``./``）与 ``docker://`` 不在此列。
- Dockerfile 的 syntax 前端与每个外部基础镜像 ``FROM`` 都带 ``@sha256:``
  digest（引用同文件前序 stage 的 FROM 除外）。
- 依赖漏洞审计 ``make audit`` 在 nightly-gate 的 deps-audit job 常态化执行
  （依赖外网漏洞库、结论随时间变化，不进 PR 门禁）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_DIR = ROOT / ".github" / "workflows"
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
_VERSION_COMMENT = re.compile(r"^\s+#\s+\S+")
_SHA256 = re.compile(r"@sha256:[0-9a-f]{64}(\s|$)")


def _workflow_files() -> list[Path]:
    files = sorted(_WORKFLOW_DIR.glob("*.yml")) + sorted(_WORKFLOW_DIR.glob("*.yaml"))
    assert files, "未找到任何 workflow 文件"
    return files


def test_every_workflow_action_is_pinned_to_a_commit_sha() -> None:
    unpinned: list[str] = []
    seen = 0
    for workflow in _workflow_files():
        for lineno, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = _USES.match(line)
            if match is None:
                continue
            ref, rest = match.groups()
            if ref.startswith(("./", "docker://")):
                continue
            seen += 1
            if not _PINNED.match(ref) or not _VERSION_COMMENT.match(rest):
                unpinned.append(f"{workflow.name}:{lineno}: {line.strip()}")
    assert seen, "workflow 中未解析到任何 uses:（正则需跟随写法更新）"
    assert not unpinned, "以下 action 未按 `@<40位sha> # <版本>` 钉死:\n" + "\n".join(unpinned)


def test_dockerfile_base_images_are_digest_pinned() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    syntax = text.splitlines()[0]
    assert syntax.startswith("# syntax="), "Dockerfile 首行 syntax 指令缺失"
    assert _SHA256.search(syntax), f"syntax 前端未钉 digest: {syntax}"

    stages: set[str] = set()
    unpinned: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].upper() != "FROM":
            continue
        args = [part for part in parts[1:] if not part.startswith("--")]
        image = args[0]
        if image not in stages and not _SHA256.search(image + " "):
            unpinned.append(line.strip())
        if len(args) >= 3 and args[1].upper() == "AS":
            stages.add(args[2])
    assert stages, "Dockerfile 未解析到任何命名 stage"
    assert not unpinned, "以下基础镜像未钉 digest:\n" + "\n".join(unpinned)


def test_dependabot_keeps_sha_pinned_actions_updatable() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {entry["package-ecosystem"]: entry for entry in config["updates"]}
    assert "github-actions" in ecosystems
    assert ecosystems["github-actions"]["schedule"]["interval"] == "weekly"


def test_dependency_audit_runs_in_nightly_gate_not_pr_gate() -> None:
    nightly = yaml.safe_load((_WORKFLOW_DIR / "nightly-gate.yml").read_text(encoding="utf-8"))
    job = nightly["jobs"]["deps-audit"]
    assert any(step.get("run") == "make audit" for step in job["steps"])
    # 漏洞库随时间变化：PR 门禁不得因上游新 CVE 随机变红。
    quality_gate = (_WORKFLOW_DIR / "quality-gate.yml").read_text(encoding="utf-8")
    assert "make audit" not in quality_gate
    assert "check-deps-audit" not in quality_gate
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^audit:.*\n\t\./scripts/check-deps-audit\.sh$", makefile, re.MULTILINE)
