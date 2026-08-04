import pytest
from pydantic import ValidationError

from server.app.routes.executor_contracts import (
    ExecutorAllocationRequest,
    NodeBindingRequest,
    NodeLimitRequest,
)


def test_executor_allocation_requires_positive_limit() -> None:
    with pytest.raises(ValidationError):
        ExecutorAllocationRequest(executor_id="pi-default", concurrency_limit=0)


def test_binding_requires_non_empty_keys() -> None:
    with pytest.raises(ValidationError):
        NodeBindingRequest(workflow_key="", node_key="review", executor_id="pi-default")


def test_node_limit_requires_positive_limit() -> None:
    with pytest.raises(ValidationError):
        NodeLimitRequest(
            workflow_key="question_comprehension_info",
            node_key="fetch_questions",
            concurrency_limit=0,
        )
