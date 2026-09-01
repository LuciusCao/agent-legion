"""Publish-time existence check for node-bound skill repos (#322).

Since the skill source registry retired, a skill's location is derived:
the in-place git repo at ``<skills root>/<group>/<name>``. A mistyped or
not-yet-imported skill key would only surface at first dispatch (a failed
job); this gate fails the PUBLISH instead. It runs as a second pass after
``validate_workflow_for_publish`` (which owns the binding/capability
semantics) so it needs its own Agent-catalog read to resolve the legacy
Agent-definition skill fallback.
"""

from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.skills.skill_roots import default_skill_base_dir
from server.app.workflows.definition import WorkflowDefinition


def skill_repo_publish_errors(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
    skill_base_dir: Path | None = None,
) -> list[str]:
    """Error per agent node whose effective skill has no in-place repo."""
    base = (skill_base_dir or default_skill_base_dir()).resolve()
    by_capability: dict[str, list] = {}
    for agent in published_agent_definitions(job_db, workspace_id).values():
        by_capability.setdefault(agent.capability, []).append(agent)
    errors: list[str] = []
    for node in definition.executable_nodes.values():
        if node.node_type != "agent":
            continue
        if node.skill is not None:
            skill_key = node.skill.key
        else:
            candidates = by_capability.get(node.capability, [])
            skill_key = candidates[0].skill if len(candidates) == 1 else ""
        if not skill_key:
            continue  # the base publish gate already reports the missing binding
        candidate = (base / skill_key).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            errors.append(f"Node {node.key} skill key escapes the skills root: {skill_key!r}")
            continue
        if not candidate.is_dir() or not (candidate / ".git").is_dir():
            errors.append(
                f"Node {node.key} binds skill {skill_key!r} but no in-place git repository "
                f"exists at {candidate} — create or clone the skill repo under the skills "
                "root (示例 workflow 的 skill 请先运行 make import-demo 导入)"
            )
    return errors
