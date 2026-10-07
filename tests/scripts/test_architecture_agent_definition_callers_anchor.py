"""Git-anchored monotonicity of the Agent catalog caller allowlist (#1033).

The allowlist (``files``) only shrinks and dropped ``symbols`` keep being
scanned, judged against the same anchors as the budget / boundary guards
(HEAD / HEAD^, ``AGENT_LEGION_BUDGET_BASE``, release-train opt-out).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.architecture.agent_definition_callers import check_agent_definition_callers
from tests.architecture_budget_helpers import commit_all

pytestmark = pytest.mark.no_db

_BASE_ENV = "AGENT_LEGION_BUDGET_BASE"
_RELEASE_TRAIN_ENV = "AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN"
_FACADE = "server/app/services/agent_node_profile_catalog.py"
_DEFINER = "server/app/services/agent_service.py"
_READER = "server/app/services/new_reader.py"
_SYMBOLS = ["has_published_agent_definitions", "published_agent_definitions"]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_allowlist(root: Path, files: list[str], symbols: list[str] = _SYMBOLS) -> None:
    _write(
        root / "config/architecture/agent-definition-catalog-callers.json",
        json.dumps({"version": 1, "symbols": symbols, "files": files}),
    )


def _repo(tmp_path: Path) -> Path:
    """Clean committed state: facade + definer allowlisted, seed commit keeps HEAD^."""
    root = tmp_path / "repo"
    _write(root / _DEFINER, "def published_agent_definitions(dsn, ws):\n    return {}\n")
    _write(
        root / _FACADE,
        "from server.app.services.agent_service import published_agent_definitions\n",
    )
    _write_allowlist(root, [_FACADE, _DEFINER])
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "test"],
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    commit_all(root, "baseline")
    return root


def _add_new_caller(root: Path) -> None:
    _write(
        root / _READER,
        "from server.app.services import agent_service\n"
        "def f(db):\n    return agent_service.published_agent_definitions(db, 'w')\n",
    )


@pytest.fixture(autouse=True)
def _clean_anchor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_BASE_ENV, raising=False)
    monkeypatch.delenv(_RELEASE_TRAIN_ENV, raising=False)


def test_clean_anchored_repo_passes(tmp_path: Path) -> None:
    assert check_agent_definition_callers(_repo(tmp_path)) == []


@pytest.mark.parametrize("commit", [False, True], ids=["uncommitted", "same-commit"])
def test_new_caller_plus_allowlist_entry_is_rejected(tmp_path: Path, commit: bool) -> None:
    """同一提交新增直连调用点并把它加进白名单：对 HEAD（未提交）或 HEAD^（已提交）锚点均拒绝。"""
    root = _repo(tmp_path)
    _add_new_caller(root)
    _write_allowlist(root, [_FACADE, _DEFINER, _READER])
    if commit:
        commit_all(root, "smuggle a new caller")

    errors = check_agent_definition_callers(root)

    assert len(errors) == 1
    assert f"entry {_READER} is not in the allowlist at git anchor" in errors[0]
    assert "#1033" in errors[0]


def test_dropping_a_symbol_does_not_release_its_new_callers(tmp_path: Path) -> None:
    """从 symbols 删掉被调用的符号也不能放行新调用点：锚点上的符号仍参与扫描。"""
    root = _repo(tmp_path)
    _add_new_caller(root)
    _write_allowlist(root, [_FACADE, _DEFINER], symbols=["has_published_agent_definitions"])
    commit_all(root, "drop the symbol and add a caller")

    errors = check_agent_definition_callers(root)

    assert [e.split(":")[0] for e in errors] == [_READER]


def test_retired_symbol_without_references_needs_no_ceremony(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(root / _DEFINER, "def has_published_agent_definitions(dsn, ws):\n    return False\n")
    _write(
        root / _FACADE,
        "from server.app.services.agent_service import has_published_agent_definitions\n",
    )
    _write_allowlist(root, [_FACADE, _DEFINER], symbols=["has_published_agent_definitions"])
    commit_all(root, "retire published_agent_definitions")

    assert check_agent_definition_callers(root) == []


def test_renamed_allowlisted_file_carries_its_entry(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    renamed = "server/app/services/agent_node_profile_loader.py"
    subprocess.run(["git", "mv", _FACADE, renamed], cwd=root, check=True)
    _write_allowlist(root, [renamed, _DEFINER])

    assert check_agent_definition_callers(root) == []


def test_base_anchor_override_sees_entry_buried_under_later_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    subprocess.run(["git", "branch", "pr-base"], cwd=root, check=True)
    _add_new_caller(root)
    _write_allowlist(root, [_FACADE, _DEFINER, _READER])
    commit_all(root, "smuggle a new caller")
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "bury"], cwd=root, check=True)
    # Default anchors HEAD / HEAD^ both already carry the entry.
    assert check_agent_definition_callers(root) == []

    monkeypatch.setenv(_BASE_ENV, "pr-base")
    errors = check_agent_definition_callers(root)
    assert len(errors) == 1
    assert "at git anchor pr-base" in errors[0]

    # Release train: anchors collapse to HEAD and take precedence (#249).
    monkeypatch.setenv(_RELEASE_TRAIN_ENV, "1")
    assert check_agent_definition_callers(root) == []


def test_unresolvable_base_anchor_hard_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv(_BASE_ENV, "origin/does-not-exist")

    errors = check_agent_definition_callers(root)

    assert any("origin/does-not-exist" in e and "does not resolve" in e for e in errors)
