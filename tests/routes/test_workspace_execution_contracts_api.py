import pytest
from pydantic import ValidationError

from server.app.routes.workspace_execution_contracts import NodeLimitRequest


def test_node_limit_requires_positive_limit() -> None:
    with pytest.raises(ValidationError):
        NodeLimitRequest(
            workflow_key="demo_workflow",
            node_key="fetch_items",
            concurrency_limit=0,
        )


def test_node_limit_requires_non_empty_keys() -> None:
    with pytest.raises(ValidationError):
        NodeLimitRequest(workflow_key="", node_key="review", concurrency_limit=1)
