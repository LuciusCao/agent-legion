"""Inline seed Agent definitions into legacy agent nodes (#935, #440 P3).

Split from ``seed_common`` (file budget). Used by ``import_seed`` step 3
before publishing a workflow's first revision.
"""

from __future__ import annotations

from typing import Any

from scripts.seed.seed_common import filter_agent_definition


def inline_agent_profiles(
    definition: dict[str, Any], agents: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """Inline the seed's Agent definitions into its legacy agent nodes (#935).

    Since #440 P3 the publish gate requires every ``agent`` node to carry its
    own execution profile, so a seed exported before the v93 backfill (agent
    nodes resolving an Agent by capability) would no longer publish. Same
    rules as the v93 migration (``agent_profile_backfill_rules``): the
    capability's unique seed Agent is inlined into the node; returns the new
    definition and the node keys left untouched (no / several Agents, or an
    unportable skill) — those fail the publish gate with its own message.
    """
    import copy

    from server.app.agent_catalog import AgentDefinition
    from server.app.db.migrations.agent_profile_backfill_rules import (
        PublishedAgent,
        backfill_blocker,
        backfill_draft_node,
        backfill_revision_node,
        effective_runtime,
    )

    by_capability: dict[str, list[PublishedAgent]] = {}
    for agent in agents:
        parsed = AgentDefinition.model_validate(filter_agent_definition(agent["definition"]))
        by_capability.setdefault(parsed.capability, []).append(
            PublishedAgent(
                agent_id=str(agent.get("agent_id") or ""),
                version=int(agent.get("version") or 0),
                capability=parsed.capability,
                runtime=parsed.runtime,
                tools=tuple(parsed.tools),
                requires_labels=dict(parsed.requires_labels),
                config_schema=dict(parsed.config_schema),
                skill=parsed.skill,
                definition_hash=parsed.definition_hash(),
            )
        )
    result = copy.deepcopy(definition)
    untouched: list[str] = []
    for node_key, node in sorted((result.get("nodes") or {}).items()):
        if not isinstance(node, dict):
            continue
        asdict_shape = "node_type" in node
        if node.get("node_type" if asdict_shape else "type") != "agent":
            continue
        if effective_runtime(node, result.get("execution")):
            continue
        matches = by_capability.get(str(node.get("capability") or ""), [])
        if len(matches) != 1 or backfill_blocker(node, matches[0]):
            untouched.append(str(node_key))
            continue
        if asdict_shape:
            backfill_revision_node(node, matches[0])
        else:
            backfill_draft_node(node, matches[0])
    return result, untouched
