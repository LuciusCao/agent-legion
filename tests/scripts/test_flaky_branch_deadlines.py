"""nightly exemption-expiry 显式检查在维护分支的 flaky 注册表 deadline（#1024）。

定时 workflow 只在默认分支运行；``scripts/quality/flaky_branch_deadlines.py``
从 git 读未发布 release/* 的注册表（宽松解析，兼容 #941 前 schema；develop
分支已随 trunk-based 迁移退役，#1150）。
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.quality.flaky_branch_deadlines import (
    deadline_findings,
    main,
    maintained_branches,
)

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 6)


def test_maintained_branches_are_unreleased_trains() -> None:
    branches = [
        "develop",
        "release/0.7.13",
        "release/0.7.15",
        "release/0.7.16",
        "release/0.8.0",
        "release/0.10.0",
        "release/next",
    ]
    assert maintained_branches(branches, (0, 7, 15)) == [
        "release/0.10.0",  # numeric, not lexicographic, comparison
        "release/0.7.16",
        "release/0.8.0",
        "release/next",  # unparsable name: kept (fail towards checking)
    ]  # develop 已退役（#1150）：残留的同名远端 ref 一律忽略


def test_deadline_findings_on_old_schema_entries() -> None:
    """#941 前的旧 schema（无 registered_on）照样判 deadline；recurring 条目跳过。"""
    entries = {
        "FLAKY-1": {"id": "FLAKY-1", "nodeid": "t::a", "deadline": date(2026, 10, 5)},
        "FLAKY-2": {"id": "FLAKY-2", "nodeid": "t::b", "deadline": "2026-10-10"},
        "FLAKY-3": {"id": "FLAKY-3", "scope": "ci", "recurring": True},
        "FLAKY-4": {"id": "FLAKY-4", "nodeid": "t::d", "deadline": "2026-12-01"},
        "FLAKY-5": {"id": "FLAKY-5", "nodeid": "t::e", "deadline": "not-a-date"},
    }
    expired, soon = deadline_findings(entries, TODAY)
    assert [entry_id for entry_id, _ in expired] == ["FLAKY-1", "FLAKY-5"]
    assert soon == [("FLAKY-2", date(2026, 10, 10))]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_registry(root: Path, entries: list[dict]) -> str:
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/flaky_registry.yaml").write_text(
        yaml.safe_dump({"entries": entries}), encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "registry")
    return _git(root, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nversion = "0.7.15"\n', encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    return root


def test_main_checks_only_maintained_branch_registries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path)
    expired = {"id": "FLAKY-9", "nodeid": "t::x", "deadline": "2026-09-01"}
    fine = {"id": "FLAKY-8", "nodeid": "t::y", "deadline": "2026-12-01"}
    shipped = _commit_registry(root, [expired])
    unreleased = _commit_registry(root, [fine])
    # Remote-tracking refs as the workflow's fetch step leaves them; a stale
    # develop ref (retired branch, #1150) must stay ignored.
    _git(root, "update-ref", "refs/remotes/origin/release/0.7.15", shipped)
    _git(root, "update-ref", "refs/remotes/origin/release/0.7.16", unreleased)
    _git(root, "update-ref", "refs/remotes/origin/develop", unreleased)

    assert main(["--root", str(root), "--today", TODAY.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "release/0.7.15" not in out  # shipped train: the default branch covers it
    assert "develop" not in out  # retired branch: never selected

    stale_train = _commit_registry(root, [expired, fine])
    _git(root, "update-ref", "refs/remotes/origin/release/0.8.0", stale_train)
    assert main(["--root", str(root), "--today", TODAY.isoformat()]) == 1
    out = capsys.readouterr().out
    assert "FAIL: release/0.8.0: FLAKY-9 deadline 2026-09-01 expired" in out


def test_branch_without_registry_is_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "no registry")
    _git(root, "update-ref", "refs/remotes/origin/release/0.9.0", _git(root, "rev-parse", "HEAD"))

    assert main(["--root", str(root), "--today", TODAY.isoformat()]) == 0
    assert "release/0.9.0: no tests/flaky_registry.yaml; skipped" in capsys.readouterr().out


def test_nightly_exemption_expiry_fetches_and_checks_maintained_branches() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/nightly-gate.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["exemption-expiry"]["steps"]
    runs = [str(step.get("run", "")) for step in steps]
    fetch = next(index for index, run in enumerate(runs) if "refs/heads/release/*" in run)
    check = next(
        index for index, run in enumerate(runs) if "scripts.quality.flaky_branch_deadlines" in run
    )
    assert fetch < check
    assert "refs/heads/release/*" in runs[fetch]
    assert "refs/heads/develop" not in runs[fetch]  # develop 分支已退役（#1150）
