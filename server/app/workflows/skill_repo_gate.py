"""Publish-time existence check for node-bound skill repos (#322, warnings #542).

Since the skill source registry retired, a skill's location is derived:
the in-place git repo at ``<skills root>/<group>/<name>``. A mistyped or
not-yet-imported skill key would only surface at first dispatch (a failed
job); this gate fails the PUBLISH instead. It runs as a second pass after
``validate_workflow_for_publish`` (which owns the binding/capability
semantics) so it needs its own Agent-catalog read to resolve the legacy
Agent-definition skill fallback.

#542 adds the advisory pass: an agent node whose effective skill carries
NO machine contract (neither root ``contract.yaml`` nor the deprecated
embedded block) yields a publish WARNING — surfaced on the publish-request
review UI, never blocking the publish (external imports must keep
running). Errors and warnings share the node→skill resolution walk
(``_agent_node_skills``) so the two lists cannot drift.
"""

from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.skills.contract_probe import probe_contract
from server.app.skills.skill_roots import default_skill_base_dir
from server.app.workflows.definition import WorkflowDefinition


def _agent_node_skills(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
) -> list[tuple[str, str]]:
    """(node_key, skill_key) for every agent node with an effective skill.

    Shared by the error and warning passes (#542): nodes without any
    resolvable skill are skipped — the base publish gate already reports
    the missing binding, and an unbound node has no skill contract to
    warn about.
    """
    by_capability: dict[str, list] = {}
    for agent in published_agent_definitions(job_db, workspace_id).values():
        by_capability.setdefault(agent.capability, []).append(agent)
    pairs: list[tuple[str, str]] = []
    for node in definition.executable_nodes.values():
        if node.node_type != "agent":
            continue
        if node.skill is not None:
            skill_key = node.skill.key
        else:
            candidates = by_capability.get(node.capability, [])
            skill_key = candidates[0].skill if len(candidates) == 1 else ""
        if skill_key:
            pairs.append((node.key, skill_key))
    return pairs


def skill_repo_publish_errors(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
    skill_base_dir: Path | None = None,
) -> list[str]:
    """Error per agent node whose effective skill has no in-place repo."""
    base = (skill_base_dir or default_skill_base_dir()).resolve()
    errors: list[str] = []
    for node_key, skill_key in _agent_node_skills(definition, workspace_id, job_db):
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


def skill_repo_publish_warnings(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
    skill_base_dir: Path | None = None,
) -> list[str]:
    """#542 advisory pass: one warning per agent node whose effective skill
    declares no machine contract (no root ``contract.yaml``, no embedded
    block). Advisory only — never blocks the publish; the human sees it on
    the publish review dialog."""
    base = (skill_base_dir or default_skill_base_dir()).resolve()
    warnings: list[str] = []
    for node_key, skill_key in _agent_node_skills(definition, workspace_id, job_db):
        candidate = (base / skill_key).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue  # the error pass already reports the escape
        if probe_contract(candidate) == "none":
            warnings.append(
                f"Node {node_key} binds skill {skill_key!r} which declares no "
                "machine-readable contract (no contract.yaml); its runtime output "
                "validation degrades to existence-only"
            )
    return warnings
