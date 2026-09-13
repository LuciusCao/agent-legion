"""Publish-time existence check for node-bound skill repos (#322, warnings #542).

Since the skill source registry retired, a skill's location is derived:
the in-place git repo at ``<skills root>/<group>/<name>``. A mistyped or
not-yet-imported skill key would only surface at first dispatch (a failed
job); this gate fails the PUBLISH instead. It runs as a second pass after
``validate_workflow_for_publish`` (which owns the binding/capability
semantics) so it needs its own Agent-catalog read to resolve the legacy
Agent-definition skill fallback.

#542 adds the advisory pass (warnings, never blocking): an agent node
whose effective skill carries no machine contract warns on the review
UI. Errors and warnings share ``_agent_node_skills``. codex R3: the
advisory probes the ref the node runs — a pinned tag reads that tag's
tree (``git show``), only ``latest`` probes the working tree.
"""

from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.agent_service import published_agent_definitions
from server.app.services.skill_repo import contract_declared_at_ref
from server.app.skills.config import LATEST_REF
from server.app.skills.contract_probe import probe_contract
from server.app.skills.skill_roots import default_skill_base_dir
from server.app.workflows.definition import WorkflowDefinition


def _agent_node_skills(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
) -> list[tuple[str, str, str]]:
    """(node_key, skill_key, ref) per agent node with an effective skill;
    the legacy Agent-definition fallback runs ``latest``. Unbound nodes
    are skipped — the base publish gate reports the missing binding.
    """
    by_capability: dict[str, list] = {}
    for agent in published_agent_definitions(job_db, workspace_id).values():
        by_capability.setdefault(agent.capability, []).append(agent)
    triples: list[tuple[str, str, str]] = []
    for node in definition.executable_nodes.values():
        if node.node_type != "agent":
            continue
        if node.skill is not None:
            skill_key = node.skill.key
            ref = node.skill.ref
        else:
            candidates = by_capability.get(node.capability, [])
            skill_key = candidates[0].skill if len(candidates) == 1 else ""
            ref = LATEST_REF
        if skill_key:
            triples.append((node.key, skill_key, ref))
    return triples


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


def skill_repo_publish_warnings(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
    skill_base_dir: Path | None = None,
) -> list[str]:
    """#542 advisory pass: one warning per agent node whose effective skill
    declares no machine contract (no root ``contract.yaml``, no embedded
    block), checked at the ref the node runs. Advisory only — never blocks
    the publish; the human sees it on the publish review dialog."""
    base = (skill_base_dir or default_skill_base_dir()).resolve()
    warnings: list[str] = []
    for node_key, skill_key, ref in _agent_node_skills(definition, workspace_id, job_db):
        candidate = (base / skill_key).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue  # the error pass already reports the escape
        if ref == LATEST_REF:
            declared = probe_contract(candidate) != "none"
        else:
            declared = contract_declared_at_ref(candidate, ref)
        if not declared:
            warnings.append(
                f"Node {node_key} binds skill {skill_key!r}"
                + (f" at ref {ref!r}" if ref != LATEST_REF else "")
                + " which declares no machine-readable contract (no contract.yaml); its "
                "runtime output validation degrades to existence-only"
            )
    return warnings
