from __future__ import annotations

from typing import Any

from server.app.services.workflow_revision_format import definition_from_job_snapshot

UNAVAILABLE_SKILL_VERSION = "unavailable"


def configured_skill_fallbacks(
    job: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, str]:
    """Return configured skill-backed nodes, excluding local-only nodes."""
    definition = definition_from_job_snapshot(job or {})
    if definition is None:
        return {}
    settings = context.get("settings")
    executor_definitions = getattr(settings, "executor_definitions", {})
    skill_by_capability: dict[str, str] = {}
    for executor in executor_definitions.values():
        for capability, config in executor.capabilities.items():
            skill = getattr(config, "skill", "")
            if skill:
                skill_by_capability.setdefault(capability, f"configured:{skill}")
    return {
        node.key: skill_by_capability[node.capability]
        for node in definition.nodes.values()
        if node.capability in skill_by_capability
    }


def job_node_fallbacks(job_id: str, job_db: Any) -> dict[str, str]:
    try:
        nodes = job_db.list_job_nodes(job_id)
    except Exception:
        return {}
    return {
        str(node["node_key"]): UNAVAILABLE_SKILL_VERSION for node in nodes if node.get("node_key")
    }
