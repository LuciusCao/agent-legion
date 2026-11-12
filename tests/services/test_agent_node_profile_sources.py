"""Agent node profile sources (#933, EXEC-AGENT-PROFILE-001): node vs legacy agent_definition."""

from __future__ import annotations

from dataclasses import replace

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.agent_catalog.definition import DEFAULT_TOOLS
from server.app.services.agent_node_profile import (
    resolve_agent_node_profile,
)
from server.app.services.agent_node_profile_types import (
    node_profile_error,
    profile_from_node,
)
from server.app.services.node_config import workflow_node_config_schemas
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from server.app.workflows.schema import WorkflowNodeExecution

pytestmark = pytest.mark.no_db

_SCHEMA = {"type": "object", "properties": {"tone": {"type": "string", "default": "calm"}}}
_CATALOG = {
    "agent-1": AgentDefinition(
        capability="generate", runtime="pi", skill="group/legacy", tools=("bash",)
    )
}


def _node(*, runtime: str = "", **fields) -> WorkflowNode:
    return WorkflowNode(
        key="gen",
        label="gen",
        capability="generate",
        node_type="agent",
        outputs=["o.json"],
        execution=WorkflowNodeExecution(runtime=runtime),
        **fields,
    )


def test_self_contained_node_resolves_to_node_source_even_with_a_published_agent() -> None:
    node = _node(
        runtime="velites",
        tools=("read",),
        requires_labels={"arch": "arm64"},
        config_schema=_SCHEMA,
    )

    profile = resolve_agent_node_profile(node, _CATALOG)

    assert profile is not None
    assert profile.source == "node"
    assert profile.legacy_ref is None
    assert profile.runtime == "velites"
    assert profile.tools == ("read",)
    assert dict(profile.requires_labels) == {"arch": "arm64"}
    assert dict(profile.config_schema) == _SCHEMA
    assert profile.skill == ""  # the node's own skill binding is the only source


def test_legacy_node_keeps_the_agent_definition_source() -> None:
    profile = resolve_agent_node_profile(_node(), _CATALOG)

    assert profile is not None
    assert profile.source == "agent_definition"
    assert profile.legacy_ref is not None and profile.legacy_ref.agent_id == "agent-1"
    assert profile.identity_hash() == _CATALOG["agent-1"].definition_hash()


def test_self_contained_node_needs_no_catalog() -> None:
    profile = resolve_agent_node_profile(_node(runtime="pi"), {})

    assert profile is not None and profile.source == "node"
    assert profile.tools == DEFAULT_TOOLS  # undeclared tools = the default set


def test_profile_hash_tracks_every_profile_field() -> None:
    base = _node(runtime="velites", requires_labels={"arch": "arm64"})
    same = profile_from_node(base).identity_hash()

    assert profile_from_node(base).identity_hash() == same
    for changed in (
        replace(base, execution=WorkflowNodeExecution(runtime="pi")),
        replace(base, requires_labels={"arch": "x86_64"}),
        replace(base, tools=("read",)),
        replace(base, config_schema=_SCHEMA),
    ):
        assert profile_from_node(changed).identity_hash() != same
    # provider/model are not profile fields (they stay in the execution block).
    assert (
        profile_from_node(
            replace(base, execution=WorkflowNodeExecution(runtime="velites", model="m"))
        ).identity_hash()
        == same
    )


def test_half_filled_profile_is_a_publish_error() -> None:
    assert node_profile_error(_node(requires_labels={"gpu": "yes"})) is not None
    assert node_profile_error(_node(runtime="pi", requires_labels={"gpu": "yes"})) is None
    assert node_profile_error(_node()) is None


def test_self_contained_node_config_schema_comes_from_the_node() -> None:
    """Legacy agent nodes ignore their own config_schema; self-contained ones use it."""
    legacy = replace(_node(config_schema=_SCHEMA), key="legacy")
    contained = replace(_node(runtime="velites", config_schema=_SCHEMA), key="contained")
    definition = WorkflowDefinition(
        key="wf",
        label="wf",
        intake=WorkflowIntake(),
        nodes={"legacy": legacy, "contained": contained},
    )

    schemas = workflow_node_config_schemas(definition, {})

    assert "tone" in schemas["contained"]["properties"]
    assert "tone" not in schemas.get("legacy", {}).get("properties", {})
