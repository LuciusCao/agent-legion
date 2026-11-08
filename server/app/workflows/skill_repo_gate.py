"""Publish-time existence check for node-bound skill repos (#322, warnings #542).

A skill's location is the in-place git repo at ``<skills root>/<group>/<name>``.
This gate fails publish when that repo is absent instead of deferring the error
to first dispatch. The non-blocking machine-contract advisory lives in
``skill_repo_contract_advisories`` and shares its node-to-skill resolution walk.
"""

from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.skills.skill_roots import default_skill_base_dir
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.skill_repo_contract_advisories import (
    _agent_node_skills,
)
from server.app.workflows.skill_repo_contract_advisories import (
    skill_repo_publish_warnings as skill_repo_publish_warnings,
)


def skill_repo_publish_errors(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
    skill_base_dir: Path | None = None,
) -> list[str]:
    """Error per agent node whose effective skill has no in-place repo."""
    base = (skill_base_dir or default_skill_base_dir()).resolve()
    errors: list[str] = []
    for node_key, skill_key, _ref in _agent_node_skills(definition, workspace_id, job_db):
        candidate = (base / skill_key).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            errors.append(f"Node {node_key} skill key escapes the skills root: {skill_key!r}")
            continue
        if not candidate.is_dir() or not (candidate / ".git").is_dir():
            errors.append(
                f"Node {node_key} binds skill {skill_key!r} but no in-place git repository "
                f"exists at {candidate} — create or clone the skill repo under the skills "
                "root (示例 workflow 的 skill 请先运行 make import-demo 导入)"
            )
    return errors
