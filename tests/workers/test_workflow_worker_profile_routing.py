"""Worker-side routing and scan gates for self-contained agent nodes (#933)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.app.services import agent_node_profile_catalog
from server.app.workflow_worker.routing import resolve_node_route
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.schema import WorkflowNodeExecution

pytestmark = pytest.mark.no_db


def _node(runtime: str) -> WorkflowNode:
    return WorkflowNode(
        key="draft",
        label="draft",
        capability="draft",
        node_type="agent",
        outputs=["o.json"],
        execution=WorkflowNodeExecution(runtime=runtime),
    )


def test_self_contained_node_routes_to_itself_without_db_or_cache() -> None:
    worker = MagicMock()
    worker.state.route_cache = {}
    worker.job_db._connect_read.side_effect = AssertionError("no DB on the node route")

    route = resolve_node_route(worker, "ws", "ws", _node("velites"))

    assert (route.kind, route.target_id, route.profile_source) == ("agent", "draft", "node")
    # Never cached: a legacy job sharing the node key must not inherit it.
    assert worker.state.route_cache == {}


def test_self_contained_route_requires_agent_dispatch() -> None:
    worker = MagicMock()
    worker.agent_dispatch = None
    with pytest.raises(RuntimeError, match="Agent dispatch service is not configured"):
        resolve_node_route(worker, "ws", "ws", _node("pi"))


@pytest.mark.parametrize(
    ("published", "self_contained", "expected"),
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_scan_gate_probe_opens_on_either_source(
    published: bool, self_contained: bool, expected: bool
) -> None:
    """thread.py / agent_gate.py scan only when SOME agent profile can exist:
    a published Agent OR an active revision with a self-contained node."""
    with (
        patch.object(
            agent_node_profile_catalog,
            "has_published_agent_definitions",
            return_value=published,
        ),
        patch.object(
            agent_node_profile_catalog,
            "has_self_contained_agent_nodes",
            return_value=self_contained,
        ),
    ):
        assert agent_node_profile_catalog.agent_profiles_may_exist("dsn") is expected


def _legacy_node() -> WorkflowNode:
    return WorkflowNode(
        key="draft", label="draft", capability="draft", node_type="agent", outputs=["o.json"]
    )


@pytest.mark.parametrize(
    ("catalog", "expected"),
    [({"drafter": "draft"}, ("agent", "drafter")), ({}, ("error", ""))],
)
def test_route_less_legacy_agent_node_never_falls_into_the_code_pool(
    catalog: dict[str, str], expected: tuple[str, str]
) -> None:
    """PR #1039 codex R3: the active revision made the node self-contained
    (no route row) while this job's frozen snapshot keeps the legacy node —
    resolve its Agent by capability, or fail with an actionable message."""
    from server.app.agent_catalog import AgentDefinition
    from server.app.workflow_worker import routing
    from server.app.workflow_worker.routing import NodeRoute

    definitions = {
        agent_id: AgentDefinition(capability=capability, runtime="pi")
        for agent_id, capability in catalog.items()
    }
    worker = MagicMock()
    worker.state.route_cache = {}
    with (
        patch(
            "server.app.workflow_worker.routing_cache._resolve_uncached",
            return_value=NodeRoute("executor", target_id="code"),
        ),
        patch.object(routing, "legacy_agent_catalog", return_value=definitions),
    ):
        route = resolve_node_route(worker, "ws", "ws", _legacy_node())

    assert (route.kind, route.target_id) == expected
    if route.kind == "error":
        assert "upgrade the job" in route.error_message
