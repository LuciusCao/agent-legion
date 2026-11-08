"""Publish-time machine-contract advisories for node-bound skill repos."""

from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.agent_node_profile import (
    build_capability_index,
    resolve_agent_node_profile,
)
from server.app.services.agent_node_profile_catalog import legacy_agent_catalog
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
    """Return ``(node_key, skill_key, ref)`` for each effective skill binding.

    The legacy Agent-definition fallback runs ``latest``. Unbound nodes are
    skipped because the base publish gate reports the missing binding.
    """
    catalog = legacy_agent_catalog(job_db, workspace_id)
    index = build_capability_index(catalog)
    triples: list[tuple[str, str, str]] = []
    for node in definition.executable_nodes.values():
        if node.node_type != "agent":
            continue
        if node.skill is not None:
            skill_key = node.skill.key
            ref = node.skill.ref
        else:
            profile = resolve_agent_node_profile(node, catalog, index=index)
            skill_key = profile.skill if profile is not None else ""
            ref = LATEST_REF
        if skill_key:
            triples.append((node.key, skill_key, ref))
    return triples


def skill_repo_publish_warnings(
    definition: WorkflowDefinition,
    workspace_id: str,
    job_db: JobQueries,
    skill_base_dir: Path | None = None,
) -> list[str]:
    """Return one non-blocking warning per node whose skill has no contract.

    Both a root ``contract.yaml`` and the deprecated embedded block declare a
    machine contract. The probe reads the ref the node runs: a pinned tag via
    the object database, ``latest`` via the working tree.
    """
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
