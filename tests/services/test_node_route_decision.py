"""Shared route decision (PR #1085): table-driven matrix over every branch.

``decide_node_route`` is the single source dispatch
(``workflow_worker/routing.resolve_node_route``) and the job-detail
projection both call; ``tests/services/test_route_decision_consistency.py``
pins that the two agree on a live database.
"""

from __future__ import annotations

from typing import Any

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.node_route_decision import RouteDecision, decide_node_route
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.schema import WorkflowNodeExecution

pytestmark = pytest.mark.no_db

_CATALOG = {
    "drafter": AgentDefinition(capability="draft", runtime="pi"),
    "other": AgentDefinition(capability="other", runtime="pi"),
}


def _node(node_type: str = "agent", runtime: str = "") -> WorkflowNode:
    return WorkflowNode(
        key="draft",
        label="draft",
        capability="draft",
        node_type=node_type,
        outputs=["o.json"],
        execution=WorkflowNodeExecution(runtime=runtime),
    )


def _row(target: str, kind: str = "agent") -> dict[str, Any]:
    return {"target_kind": kind, "target_id": target}


@pytest.mark.parametrize(
    ("node", "row", "catalog", "expected"),
    [
        # 1. self-contained → itself, whatever the route / catalog says.
        (_node(runtime="velites"), None, {}, ("agent", "draft", "node")),
        (_node(runtime="velites"), _row("drafter"), _CATALOG, ("agent", "draft", "node")),
        # 2. Agent route → validated published Agent.
        (_node(), _row("drafter"), _CATALOG, ("agent", "drafter", "agent_definition")),
        (_node(), _row("ghost"), _CATALOG, ("error", "", "agent_definition")),
        (_node(), _row("other"), _CATALOG, ("error", "", "agent_definition")),
        # A code node routed to an Agent follows the route (#1091 gap).
        (_node("code"), _row("drafter"), _CATALOG, ("agent", "drafter", "agent_definition")),
        # 3. route-less legacy agent → capability fallback, else error.
        (_node(), None, _CATALOG, ("agent", "drafter", "agent_definition")),
        (_node(), None, {}, ("error", "", "agent_definition")),
        # Non-Agent route rows read as no route.
        (_node(), _row("h", kind="handler"), _CATALOG, ("agent", "drafter", "agent_definition")),
        # 4. code without route → code pool.
        (_node("code"), None, _CATALOG, ("executor", "", "agent_definition")),
        (_node("code"), _row("h", kind="handler"), {}, ("executor", "", "agent_definition")),
    ],
)
def test_decision_matrix(
    node: WorkflowNode,
    row: dict[str, Any] | None,
    catalog: dict[str, AgentDefinition],
    expected: tuple[str, str, str],
) -> None:
    decision = decide_node_route(node, row, workspace_id="ws", catalog=lambda: catalog)

    assert (decision.kind, decision.target_id, decision.profile_source) == expected
    assert bool(decision.error_message) == (decision.kind == "error")


def test_catalog_is_not_read_when_no_branch_needs_it() -> None:
    def _boom() -> dict[str, AgentDefinition]:
        raise AssertionError("catalog read")

    assert decide_node_route(_node("code"), None, workspace_id="ws", catalog=_boom) == (
        RouteDecision("executor")
    )
    assert decide_node_route(_node(runtime="pi"), None, workspace_id="ws", catalog=_boom).kind == (
        "agent"
    )


def test_cached_routed_decision_is_reused_and_fallback_layered_on_top() -> None:
    routed = RouteDecision("executor")
    decision = decide_node_route(
        _node(), _row("ignored"), workspace_id="ws", catalog=lambda: _CATALOG, routed=routed
    )
    assert (decision.kind, decision.target_id) == ("agent", "drafter")
