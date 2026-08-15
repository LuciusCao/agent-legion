from pathlib import Path

import pytest

from server.app.workflows.skills import resolve_workflow_skill
from tests.helpers import load_builtin_definition

WORKFLOWS = {
    "education_video_problems_generation": {
        "intake_knowledge_points",
        "publish_content",
    },
}


def _write_minimal_skill(root: Path, skill_key: str) -> None:
    skill_dir = root / skill_key
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "SKILL.md").write_text("# fixture skill\n", encoding="utf-8")
    (skill_dir / "references" / "output-contract.md").write_text("contract\n", encoding="utf-8")
    (skill_dir / "scripts" / "validate_output.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )


@pytest.mark.parametrize(("workflow_key", "local_capabilities"), WORKFLOWS.items())
def test_all_agent_nodes_map_to_complete_skill_contracts(
    tmp_path: Path,
    workflow_key: str,
    local_capabilities: set[str],
) -> None:
    definition = load_builtin_definition(workflow_key)

    for node in definition.nodes.values():
        if node.capability in local_capabilities:
            continue
        skill_key = f"{workflow_key}/{node.capability}"
        _write_minimal_skill(tmp_path, skill_key)
        assert resolve_workflow_skill(tmp_path, skill_key) == (tmp_path / skill_key).resolve()


def test_resolve_workflow_skill_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="skill path"):
        resolve_workflow_skill(tmp_path, "../outside")


def test_resolve_workflow_skill_rejects_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="skill path"):
        resolve_workflow_skill(tmp_path, "/outside")


def test_resolve_workflow_skill_rejects_symlink_escape(tmp_path: Path) -> None:
    # Passes the upfront validation (relative, no '..') but resolves outside root.
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must remain below root"):
        resolve_workflow_skill(root, "link")


def test_resolve_workflow_skill_requires_skill_md(tmp_path: Path) -> None:
    (tmp_path / "foo").mkdir()
    with pytest.raises(ValueError, match="missing SKILL.md"):
        resolve_workflow_skill(tmp_path, "foo")


def test_resolve_workflow_skill_requires_contract_files(tmp_path: Path) -> None:
    (tmp_path / "foo").mkdir()
    (tmp_path / "foo" / "SKILL.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="output-contract"):
        resolve_workflow_skill(tmp_path, "foo")


def test_resolve_workflow_skill_requires_validate_output(tmp_path: Path) -> None:
    skill_dir = tmp_path / "foo"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("x", encoding="utf-8")
    (skill_dir / "references" / "output-contract.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="validate_output"):
        resolve_workflow_skill(tmp_path, "foo")
