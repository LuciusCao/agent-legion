"""Ref-aware skill checkout wrapper (issue #76, dispatch phase).

``resolve_skill_checkout`` pins the effective ref into the returned
``SkillCheckout`` (source default when the caller passes none, explicit ref
otherwise); ``checkout_node_skill`` applies the dispatch-time source priority
(node binding wins, Agent definition skill is the legacy fallback).
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.app.skills.checkout import (
    SkillCheckout,
    checkout_node_skill,
    resolve_skill_checkout,
)
from server.app.workflows.schema import WorkflowNode, WorkflowNodeSkill
from tests.helpers.skill_git import _KEY, _git_env, _make_bare_repo, _make_manager

pytestmark = pytest.mark.no_db


def _tag_repo(tmp_path: Path, tag: str = "v1.0.0") -> None:
    env = _git_env()
    clone = tmp_path / "work" / "clone"
    subprocess.run(["git", "-C", str(clone), "tag", tag], check=True, env=env)
    subprocess.run(["git", "-C", str(clone), "push", "origin", tag], check=True, env=env)


def _head_commit(tmp_path: Path) -> str:
    env = _git_env()
    clone = tmp_path / "work" / "clone"
    result = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def test_checkout_defaults_to_the_declared_source_ref(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    _tag_repo(tmp_path)
    manager = _make_manager(tmp_path, {_KEY: {"repo": repo_uri, "ref": "v1.0.0"}})

    checkout = resolve_skill_checkout(manager, _KEY, str(uuid.uuid4()))

    commit = _head_commit(tmp_path)
    assert checkout.commit == commit
    assert checkout.ref == "v1.0.0"
    assert checkout.version == f"v1.0.0@{commit[:12]}"
    assert (checkout.run_dir / "SKILL.md").is_file()


def test_checkout_honors_an_explicit_ref_and_freezes_it(tmp_path: Path) -> None:
    repo_uri = _make_bare_repo(tmp_path)
    _tag_repo(tmp_path)
    manager = _make_manager(tmp_path, {_KEY: {"repo": repo_uri, "ref": "main"}})

    checkout = resolve_skill_checkout(manager, _KEY, str(uuid.uuid4()), "v1.0.0")

    commit = _head_commit(tmp_path)
    assert checkout.ref == "v1.0.0"
    assert checkout.version == f"v1.0.0@{commit[:12]}"
    # The explicit ref auto-locks alongside the declared default ref.
    locked = manager.load_lock().skills[_KEY]
    assert locked.refs["v1.0.0"] == commit


def _contract_skill_tree(base_dir: Path, key: str) -> None:
    skill_dir = base_dir / key
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "SKILL.md").write_text("# skill\n")
    (skill_dir / "references" / "output-contract.md").write_text("contract\n")
    (skill_dir / "scripts" / "validate_output.py").write_text("print('ok')\n")


def _mock_manager(tmp_path: Path, key: str, ref: str) -> MagicMock:
    manager = MagicMock()
    manager.base_dir = tmp_path / "skills"
    _contract_skill_tree(manager.base_dir, key)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manager.checkout_skill.return_value = (run_dir, "c" * 40, f"{ref}@{'c' * 12}")
    return manager


def _node_with_skill(key: str, ref: str) -> WorkflowNode:
    return WorkflowNode(
        key="do", label="Do", capability="cap", skill=WorkflowNodeSkill(key=key, ref=ref)
    )


def test_checkout_node_skill_prefers_the_node_binding(tmp_path: Path) -> None:
    manager = _mock_manager(tmp_path, "group/node-skill", "v2")

    checkout = checkout_node_skill(
        manager, _node_with_skill("group/node-skill", "v2"), "group/agent-skill", "exec-1"
    )

    manager.checkout_skill.assert_called_once_with("group/node-skill", "exec-1", "v2")
    assert checkout == SkillCheckout(
        key="group/node-skill",
        ref="v2",
        run_dir=tmp_path / "run",
        commit="c" * 40,
        version=f"v2@{'c' * 12}",
    )


def test_checkout_node_skill_falls_back_to_the_agent_definition(tmp_path: Path) -> None:
    manager = _mock_manager(tmp_path, "group/agent-skill", "v1")
    node = WorkflowNode(key="do", label="Do", capability="cap")

    checkout_node_skill(manager, node, "group/agent-skill", "exec-2")

    manager.checkout_skill.assert_called_once_with("group/agent-skill", "exec-2", None)


def test_checkout_node_skill_raises_when_neither_side_binds(tmp_path: Path) -> None:
    manager = _mock_manager(tmp_path, "group/agent-skill", "v1")
    node = WorkflowNode(key="do", label="Do", capability="cap")

    with pytest.raises(ValueError, match="no skill on node or Agent definition"):
        checkout_node_skill(manager, node, "", "exec-3")

    manager.checkout_skill.assert_not_called()
