"""Workspace node-limit validation (P-0.5: the only per-node execution knob)."""

from __future__ import annotations

import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.services.workspace_node_limit_validation import (
    validate_workspace_node_limits,
)
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)

pytestmark = pytest.mark.no_db


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="demo_workflow",
        label="Reading Analysis",
        intake=WorkflowIntake(),
        nodes={
            "fetch_items": WorkflowNode(
                key="fetch_items",
                label="Fetch Questions",
                capability="fetch_items",
            ),
            "review_keywords": WorkflowNode(
                key="review_keywords",
                label="Review Keywords",
                capability="review_keywords",
                node_type="agent",
            ),
        },
    )


def _limit(node_key: str, concurrency_limit: int, workflow_key: str = "demo_workflow") -> dict:
    return {
        "workflow_key": workflow_key,
        "node_key": node_key,
        "concurrency_limit": concurrency_limit,
    }


def test_valid_node_limits_pass() -> None:
    validate_workspace_node_limits(
        workflow=_workflow(),
        node_limits=[_limit("fetch_items", 2)],
        code_capacity=16,
    )


def test_duplicate_node_limits_are_rejected() -> None:
    with pytest.raises(InvalidOperationError, match="Duplicate Node limit"):
        validate_workspace_node_limits(
            workflow=_workflow(),
            node_limits=[_limit("fetch_items", 1), _limit("fetch_items", 2)],
            code_capacity=16,
        )


def test_limit_above_code_capacity_is_rejected() -> None:
    with pytest.raises(InvalidOperationError, match="code pool capacity"):
        validate_workspace_node_limits(
            workflow=_workflow(),
            node_limits=[_limit("fetch_items", 17)],
            code_capacity=16,
        )


def test_unknown_workflow_node_is_rejected() -> None:
    with pytest.raises(InvalidOperationError, match="Unknown Workflow Node"):
        validate_workspace_node_limits(
            workflow=_workflow(),
            node_limits=[_limit("missing_node", 1)],
            code_capacity=16,
        )
    with pytest.raises(InvalidOperationError, match="Unknown Workflow Node"):
        validate_workspace_node_limits(
            workflow=_workflow(),
            node_limits=[_limit("fetch_items", 1, workflow_key="other_flow")],
            code_capacity=16,
        )


def test_agent_routed_node_cannot_have_a_limit() -> None:
    with pytest.raises(InvalidOperationError, match="Agent-routed Node"):
        validate_workspace_node_limits(
            workflow=_workflow(),
            node_limits=[_limit("review_keywords", 1)],
            code_capacity=16,
        )


def test_code_node_may_have_a_limit_despite_matching_agent_capability() -> None:
    """Explicit types (#284): a type=code node may share its capability with a
    published Agent (the Agent stays idle) and still carry a node limit."""
    from dataclasses import replace

    workflow = _workflow()
    node = workflow.nodes["review_keywords"]
    object.__setattr__(
        workflow,
        "nodes",
        {**workflow.nodes, "review_keywords": replace(node, node_type="code")},
    )
    validate_workspace_node_limits(
        workflow=workflow,
        node_limits=[_limit("review_keywords", 1)],
        code_capacity=16,
    )


def test_definitionless_workflow_only_checks_generic_rules() -> None:
    """Registered workflow before its first publish: node existence/routing
    checks defer to publish-time validation (first-publish chicken-and-egg)."""
    validate_workspace_node_limits(
        workflow=None,
        node_limits=[_limit("anything", 1)],
        code_capacity=16,
    )
    with pytest.raises(InvalidOperationError, match="Duplicate Node limit"):
        validate_workspace_node_limits(
            workflow=None,
            node_limits=[_limit("a", 1), _limit("a", 2)],
            code_capacity=16,
        )
