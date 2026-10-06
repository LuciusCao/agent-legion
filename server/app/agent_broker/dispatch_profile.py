"""Request-row profile columns for Agent dispatch (#933, schema v92).

Split from ``dispatch`` (file budget). Legacy (Agent-definition) rows leave
the columns NULL, so they differ from their pre-P2 shape only by the
defaulted ``profile_source``; self-contained node rows carry the frozen
runtime / requires_labels the claim reads instead of joining
``versioned_entities``.
"""

from __future__ import annotations

from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_node_profile_types import PROFILE_SOURCE_NODE


def profile_row_fields(profile_source: str, definition: AgentDefinition) -> dict[str, Any]:
    """``AgentExecutionRequest`` keyword fields for *profile_source*."""
    if profile_source != PROFILE_SOURCE_NODE:
        return {}
    return {
        "profile_source": PROFILE_SOURCE_NODE,
        "runtime": definition.runtime,
        "requires_labels": dict(definition.requires_labels),
    }
