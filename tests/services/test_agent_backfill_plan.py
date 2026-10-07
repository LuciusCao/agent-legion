"""Agent backfill dry-run: per-node simulation rules (#934, #440 §3), pure."""

from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_backfill_plan import (
    RouteTarget,
    config_schema_diff,
    plan_definition_backfill,
)
from server.app.services.agent_node_profile import build_capability_index
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string

pytestmark = pytest.mark.no_db

_TONE = {"type": "object", "properties": {"tone": {"type": "string", "default": "calm"}}}
_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string", "default": "loud"},
        "length": {"type": "integer", "default": 3},
    },
}

_YAML = """
key: wf
label: WF
nodes:
  a_inherit:
    type: agent
    capability: write
  b_own:
    type: agent
    capability: write
    tools: [bash]
    skill: {key: grp/own, ref: v1}
    config_schema:
      type: object
      properties:
        tone: {type: string, default: loud}
        length: {type: integer, default: 3}
  c_dup:
    type: agent
    capability: dup
  d_none:
    type: agent
    capability: nobody
  e_archived:
    type: agent
    capability: legacy
  f_draft:
    type: agent
    capability: drafted
  g_code:
    capability: code_thing
"""


def _agent(capability: str, **overrides) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {"capability": capability, "runtime": "velites", **overrides}
    )


def _plan() -> dict[str, dict]:
    catalog = {
        "writer": _agent(
            "write",
            runtime="pi",
            tools=("read",),
            skill="grp/writer",
            requires_labels={"gpu": "yes", "arch": "arm64"},
            config_schema=_TONE,
        ),
        "dup-b": _agent("dup"),
        "dup-a": _agent("dup"),
    }
    unpublished = {"old": ("legacy", "archived"), "drafty": ("drafted", "draft")}
    definition = workflow_definition_from_yaml_string(_YAML)
    entries = plan_definition_backfill(
        definition, catalog, build_capability_index(catalog), unpublished
    )
    return {entry["node_key"]: entry for entry in entries}


def test_only_agent_nodes_are_planned_in_key_order() -> None:
    assert list(_plan()) == ["a_inherit", "b_own", "c_dup", "d_none", "e_archived", "f_draft"]


def test_inherited_node_takes_every_definition_field() -> None:
    entry = _plan()["a_inherit"]

    assert entry["status"] == "backfill"
    assert entry["agent_id"] == "writer"
    assert entry["runtime"] == "pi"
    assert entry["tools"] == {"value": ["read"], "source": "definition"}
    assert entry["config_schema"] == _TONE
    assert entry["config_schema_diff"] is None  # nothing declared → pure fill
    assert entry["skill"] == {"key": "grp/writer", "ref": "latest", "source": "definition"}
    assert list(entry["requires_labels"]) == ["arch", "gpu"]


def test_node_tools_and_skill_win_but_config_schema_is_overwritten() -> None:
    entry = _plan()["b_own"]

    assert entry["tools"] == {"value": ["bash"], "source": "node"}
    assert entry["skill"] == {"key": "grp/own", "ref": "v1", "source": "node"}
    assert entry["config_schema"] == _TONE
    diff = entry["config_schema_diff"]
    assert diff["node_declared"] == _NODE_SCHEMA
    assert diff["properties_removed"] == ["length"]
    assert diff["properties_changed"] == ["tone"]
    assert diff["properties_added"] == []


@pytest.mark.parametrize(
    ("node_key", "reason", "agent_ids"),
    [
        ("c_dup", "ambiguous", ["dup-a", "dup-b"]),
        ("d_none", "no_agent", []),
        ("e_archived", "archived", ["old"]),
        ("f_draft", "draft_only", ["drafty"]),
    ],
)
def test_unresolved_nodes_carry_a_reason(node_key: str, reason: str, agent_ids: list) -> None:
    entry = _plan()[node_key]

    assert entry["status"] == "unresolved"
    assert entry["reason"] == reason
    assert entry["agent_ids"] == agent_ids
    assert "runtime" not in entry


def test_missing_skill_on_both_sides_reports_none() -> None:
    catalog = {"r": _agent("review")}
    definition = workflow_definition_from_yaml_string(
        "key: wf\nlabel: WF\nnodes:\n  n:\n    type: agent\n    capability: review\n"
    )
    [entry] = plan_definition_backfill(definition, catalog, build_capability_index(catalog), {})

    assert entry["skill"] is None


def test_config_schema_diff_reports_top_level_and_wiped_declarations() -> None:
    node = {"type": "object", "required": ["tone"], "properties": {"tone": {"type": "string"}}}

    diff = config_schema_diff(node, {})

    assert diff is not None
    assert diff["properties_removed"] == ["tone"]
    assert diff["other_keys_changed"] == ["required", "type"]
    assert config_schema_diff(node, node) is None
    assert config_schema_diff({}, _TONE) is None


def test_self_contained_profile_is_not_backfilled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A profile no longer sourced from a definition (P2, #933) needs no backfill."""
    from dataclasses import replace

    from server.app.services import agent_backfill_plan

    catalog = {"r": _agent("review")}
    real = agent_backfill_plan.resolve_agent_node_profile

    def _node_sourced(*args, **kwargs):
        profile = real(*args, **kwargs)
        return replace(profile, legacy_ref=None) if profile is not None else None

    monkeypatch.setattr(agent_backfill_plan, "resolve_agent_node_profile", _node_sourced)
    definition = workflow_definition_from_yaml_string(
        "key: wf\nlabel: WF\nnodes:\n  n:\n    type: agent\n    capability: review\n"
    )

    [entry] = plan_definition_backfill(definition, catalog, build_capability_index(catalog), {})

    assert entry["status"] == "self_contained"


_ROUTED_YAML = "key: wf\nlabel: WF\nnodes:\n  n:\n    type: agent\n    capability: review\n"


def _routed(routes: dict[str, RouteTarget], catalog: dict) -> dict:
    definition = workflow_definition_from_yaml_string(_ROUTED_YAML)
    [entry] = plan_definition_backfill(
        definition, catalog, build_capability_index(catalog), {}, routes
    )
    return entry


def test_route_matching_catalog_backfills_without_drift() -> None:
    catalog = {"r": _agent("review")}

    entry = _routed({"n": RouteTarget("r", "published", catalog["r"])}, catalog)

    assert entry["status"] == "backfill"
    assert entry["route"] == {"target_id": "r", "target_status": "published"}
    assert "route_drift" not in entry


def test_missing_route_target_is_unresolved_with_drift() -> None:
    catalog = {"r2": _agent("review")}

    entry = _routed({"n": RouteTarget("gone", "missing", None)}, catalog)

    assert (entry["status"], entry["reason"], entry["agent_ids"]) == (
        "unresolved",
        "route_target_missing",
        ["gone"],
    )
    assert entry["route_drift"] == {"catalog_candidates": ["r2"]}


def test_active_node_without_route_but_with_candidate_is_drift() -> None:
    catalog = {"r": _agent("review")}

    entry = _routed({}, catalog)

    assert (entry["status"], entry["reason"]) == ("unresolved", "no_route")
    assert entry["route"] is None
    assert entry["route_drift"] == {"catalog_candidates": ["r"]}


def test_active_node_without_route_or_candidate_is_plain_unresolved() -> None:
    entry = _routed({}, {})

    assert (entry["status"], entry["reason"]) == ("unresolved", "no_agent")
    assert "route_drift" not in entry


def test_empty_definition_tools_without_node_tools_is_unresolved() -> None:
    """#935 D1: an Agent published with ``tools: []`` cannot be inlined into a
    node without tools — an empty node list means the default tier, which
    would widen permissions. Same rule as the v93 migration."""
    catalog = {"r": _agent("review", tools=())}
    definition = workflow_definition_from_yaml_string(
        "key: wf\nlabel: WF\nnodes:\n  n:\n    type: agent\n    capability: review\n"
        "  m:\n    type: agent\n    capability: review\n    tools: [read]\n"
    )
    entries = plan_definition_backfill(definition, catalog, build_capability_index(catalog), {})
    by_key = {entry["node_key"]: entry for entry in entries}

    assert by_key["n"]["status"] == "unresolved"
    assert by_key["n"]["reason"] == "tools_empty_unportable"
    assert by_key["m"]["status"] == "backfill"  # the node's own tools win
