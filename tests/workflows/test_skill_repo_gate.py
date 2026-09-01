"""#322 publish-time skill-repo existence gate (``workflows/skill_repo_gate``)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from server.app.jobs.queries import JobQueries
from server.app.workflows.definition import (
    WorkflowDefinition,
    workflow_definition_from_mapping,
)
from server.app.workflows.skill_repo_gate import skill_repo_publish_errors
from tests.postgres_support import TEST_DATABASE_URL


def _make_skill_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True, env=env)


def _definition(skill: dict | None) -> WorkflowDefinition:
    node: dict = {"type": "agent", "capability": "do_thing"}
    if skill is not None:
        node["skill"] = skill
    return workflow_definition_from_mapping({"key": "wf", "label": "Wf", "nodes": {"do": node}})


def test_missing_repo_is_reported_with_guidance(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws", default_workflow_key="wf")
    definition = _definition({"key": "group/name", "ref": "latest"})

    errors = skill_repo_publish_errors(definition, workspace["id"], queries, tmp_path / "skills")

    assert len(errors) == 1
    assert "no in-place git repository" in errors[0]
    assert "group/name" in errors[0]


def test_existing_repo_passes(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws", default_workflow_key="wf")
    skill_base = tmp_path / "skills"
    _make_skill_repo(skill_base / "group" / "name")

    assert (
        skill_repo_publish_errors(
            _definition({"key": "group/name", "ref": "v1"}),
            workspace["id"],
            queries,
            skill_base,
        )
        == []
    )


def test_skill_key_escape_is_rejected(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws", default_workflow_key="wf")
    skill_base = tmp_path / "skills"
    (skill_base / "group").mkdir(parents=True)
    (skill_base / "group" / "name").symlink_to(tmp_path, target_is_directory=True)

    errors = skill_repo_publish_errors(
        _definition({"key": "group/name"}), workspace["id"], queries, skill_base
    )

    assert len(errors) == 1
    assert "escapes the skills root" in errors[0]


def test_code_nodes_and_unbound_agent_nodes_are_skipped(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws", default_workflow_key="wf")
    definition = workflow_definition_from_mapping(
        {
            "key": "wf",
            "label": "Wf",
            "nodes": {
                "code_node": {"type": "code", "capability": "c"},
                "agent_node": {"type": "agent", "capability": "a"},
            },
        }
    )

    # No skill binding anywhere: the base publish gate reports the binding
    # problem; this pass stays silent.
    assert skill_repo_publish_errors(definition, workspace["id"], queries, tmp_path) == []
