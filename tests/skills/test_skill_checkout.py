"""Ref-aware skill checkout wrapper (issue #76, dispatch phase; #322 refs).

``resolve_skill_checkout`` pins the effective ref into the returned
``SkillCheckout`` (``latest`` — the repo's live HEAD — when the caller
passes none, an explicit tag otherwise); ``checkout_node_skill`` applies the
dispatch-time source priority (node binding wins, Agent definition skill is
the legacy fallback).
"""

from __future__ import annotations

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
from tests.helpers.skill_git import (
    _KEY,
    _head_commit,
    _make_manager,
    _make_skill_repo,
    _tag,
)

pytestmark = pytest.mark.no_db


def test_checkout_defaults_to_latest(tmp_path: Path) -> None:
    """#322: no ref is ``latest`` — the live HEAD, recorded as latest@commit12."""
    repo = _make_skill_repo(tmp_path / "skills")
    manager = _make_manager(tmp_path)

    checkout = resolve_skill_checkout(manager, _KEY, str(uuid.uuid4()))

    commit = _head_commit(repo)
    assert checkout.commit == commit
    assert checkout.ref == "latest"
    assert checkout.version == f"latest@{commit[:12]}"
    assert (checkout.run_dir / "SKILL.md").is_file()
    # The manifest triple records the normalized latest ref (#322 contract).
    assert checkout.manifest_pins() == {
        "skill": _KEY,
        "skill_ref": "latest",
        "skill_version": f"latest@{commit[:12]}",
    }
    # latest never enters the lock.
    assert manager.load_lock().skills == {}


def test_checkout_honors_an_explicit_ref_and_freezes_it(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills")
    _tag(repo, "v1.0.0")
    manager = _make_manager(tmp_path)

    checkout = resolve_skill_checkout(manager, _KEY, str(uuid.uuid4()), "v1.0.0")

    commit = _head_commit(repo)
    assert checkout.ref == "v1.0.0"
    assert checkout.version == f"v1.0.0@{commit[:12]}"
    # The explicit tag auto-locks on first dispatch.
    locked = manager.load_lock().skills[_KEY]
    assert locked.refs["v1.0.0"] == commit


def test_checkout_explicit_latest_skips_the_lock(tmp_path: Path) -> None:
    repo = _make_skill_repo(tmp_path / "skills")
    _tag(repo, "v1.0.0")
    manager = _make_manager(tmp_path)

    checkout = resolve_skill_checkout(manager, _KEY, str(uuid.uuid4()), "latest")

    assert checkout.ref == "latest"
    assert manager.load_lock().skills == {}


def _contract_skill_tree(base_dir: Path, key: str) -> None:
    skill_dir = base_dir / key
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "SKILL.md").write_text("# skill\n")
    (skill_dir / "references" / "output-contract.md").write_text("contract\n")
    (skill_dir / "scripts" / "validate_output.py").write_text("print('ok')\n")


def _mock_manager(tmp_path: Path, key: str, ref: str) -> MagicMock:
    """Manager stub: the run dir uses the real <root>/<exec>/<group>/<name>
    layout and carries the contract tree; the shared cache dir deliberately
    lacks it so tests prove validation reads the run dir (codex P1, PR 317)."""
    manager = MagicMock()
    manager.base_dir = tmp_path / "skills"
    (manager.base_dir / key).mkdir(parents=True)  # cache without SKILL.md etc.
    run_root = tmp_path / "runs" / "exec"
    _contract_skill_tree(run_root, key)
    run_dir = run_root / key
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
        run_dir=tmp_path / "runs" / "exec" / "group" / "node-skill",
        commit="c" * 40,
        version=f"v2@{'c' * 12}",
    )


def test_validation_reads_the_run_dir_not_the_shared_cache(tmp_path: Path) -> None:
    """契约校验作用于执行私有 run_dir（实际打包的内容），不是共享 cache——
    checkout 返回后另一个 dispatch 可能已把 cache 切到别的 ref（codex P1，
    PR 317）。_mock_manager 的 cache 故意缺契约文件，校验通过即证明读的是
    run_dir。"""
    manager = _mock_manager(tmp_path, "group/node-skill", "v2")

    checkout = checkout_node_skill(
        manager, _node_with_skill("group/node-skill", "v2"), "", "exec-1"
    )

    assert (checkout.run_dir / "SKILL.md").is_file()


def test_invalid_run_dir_cleans_up_and_raises(tmp_path: Path) -> None:
    """run_dir 缺契约文件时校验失败并回收执行目录（不泄漏 runs/<exec> 副本）。"""
    manager = _mock_manager(tmp_path, "group/node-skill", "v2")
    (tmp_path / "runs" / "exec" / "group" / "node-skill" / "SKILL.md").unlink()

    with pytest.raises(ValueError, match="missing SKILL.md"):
        checkout_node_skill(manager, _node_with_skill("group/node-skill", "v2"), "", "exec-9")

    manager.cleanup_execution.assert_called_once_with("exec-9")


def test_checkout_node_skill_falls_back_to_the_agent_definition(tmp_path: Path) -> None:
    """Legacy fallback: the Agent definition's skill dispatches at ``latest``
    (#322 normalization of the ref-less legacy shape)."""
    manager = _mock_manager(tmp_path, "group/agent-skill", "latest")
    node = WorkflowNode(key="do", label="Do", capability="cap")

    checkout_node_skill(manager, node, "group/agent-skill", "exec-2")

    manager.checkout_skill.assert_called_once_with("group/agent-skill", "exec-2", "latest")


def test_checkout_node_skill_raises_when_neither_side_binds(tmp_path: Path) -> None:
    manager = _mock_manager(tmp_path, "group/agent-skill", "v1")
    node = WorkflowNode(key="do", label="Do", capability="cap")

    with pytest.raises(ValueError, match="no skill on node or Agent definition"):
        checkout_node_skill(manager, node, "", "exec-3")

    manager.checkout_skill.assert_not_called()
