from __future__ import annotations

from dataclasses import dataclass

import pytest

from server.app.executors.config import (
    CodeCapabilityConfig,
    CodeExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.services.job_errors import InvalidOperationError
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)


@dataclass
class ValidationContext:
    workflow: WorkflowDefinition
    executors: dict[str, CodeExecutorConfig | PiExecutorConfig]


@pytest.fixture
def context() -> ValidationContext:
    workflow = WorkflowDefinition(
        key="question_comprehension_info",
        label="Reading Analysis",
        intake=WorkflowIntake(),
        nodes={
            "fetch_questions": WorkflowNode(
                key="fetch_questions",
                label="Fetch Questions",
                capability="fetch_questions",
            ),
            "review_keywords": WorkflowNode(
                key="review_keywords",
                label="Review Keywords",
                capability="review_keywords",
            ),
        },
    )
    executors: dict[str, CodeExecutorConfig | PiExecutorConfig] = {
        "code-default": CodeExecutorConfig(
            kind="code",
            global_capacity=4,
            capabilities={
                "fetch_questions": CodeCapabilityConfig(path="workflow_nodes/question_intake.py"),
            },
        ),
        "pi-default": PiExecutorConfig(
            kind="pi",
            global_capacity=2,
            capabilities={
                "review_keywords": PiCapabilityConfig(
                    skill="question_comprehension_info/review_key_info"
                ),
            },
        ),
    }
    return ValidationContext(workflow=workflow, executors=executors)


def allocation(executor_id: str, concurrency_limit: int) -> dict[str, object]:
    return {"executor_id": executor_id, "concurrency_limit": concurrency_limit}


def binding(
    node_key: str, executor_id: str, workflow_key: str = "question_comprehension_info"
) -> dict[str, object]:
    return {"workflow_key": workflow_key, "node_key": node_key, "executor_id": executor_id}


def node_limit(
    node_key: str, concurrency_limit: int, workflow_key: str = "question_comprehension_info"
) -> dict[str, object]:
    return {
        "workflow_key": workflow_key,
        "node_key": node_key,
        "concurrency_limit": concurrency_limit,
    }


def test_duplicate_allocation_ids_are_rejected(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(InvalidOperationError, match="Duplicate Executor allocation code-default"):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 2), allocation("code-default", 3)],
            bindings=[],
            node_limits=[],
        )


def test_unknown_executor_ids_are_rejected(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(InvalidOperationError, match="Unknown Executor unknown-exec"):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("unknown-exec", 1)],
            bindings=[],
            node_limits=[],
        )


def test_workspace_limit_must_not_exceed_global_capacity(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Workspace limit 5 exceeds code-default global capacity 4"
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 5)],
            bindings=[],
            node_limits=[],
        )


def test_duplicate_node_bindings_are_rejected(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError,
        match="Duplicate Node binding question_comprehension_info\\.fetch_questions",
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 4)],
            bindings=[
                binding("fetch_questions", "code-default"),
                binding("fetch_questions", "code-default"),
            ],
            node_limits=[],
        )


def test_binding_workflow_must_equal_workspace_workflow(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Unknown Workflow Node other_workflow\\.fetch_questions"
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 4)],
            bindings=[binding("fetch_questions", "code-default", workflow_key="other_workflow")],
            node_limits=[],
        )


def test_binding_node_must_exist_in_workflow(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError,
        match="Unknown Workflow Node question_comprehension_info\\.unknown_node",
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 4)],
            bindings=[binding("unknown_node", "code-default")],
            node_limits=[],
        )


def test_binding_executor_must_be_allocated(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Executor code-default is not allocated to this Workspace"
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[],
            bindings=[binding("fetch_questions", "code-default")],
            node_limits=[],
        )


def test_binding_rejects_executor_without_capability(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(InvalidOperationError, match="does not support capability review_keywords"):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 4)],
            bindings=[binding("review_keywords", "code-default")],
            node_limits=[],
        )


def test_duplicate_node_limits_are_rejected(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError,
        match="Duplicate Node limit question_comprehension_info\\.fetch_questions",
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 4)],
            bindings=[binding("fetch_questions", "code-default")],
            node_limits=[
                node_limit("fetch_questions", 1),
                node_limit("fetch_questions", 2),
            ],
        )


def test_node_limit_requires_existing_binding(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError,
        match="Node limit requires binding for question_comprehension_info\\.fetch_questions",
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 4)],
            bindings=[],
            node_limits=[node_limit("fetch_questions", 1)],
        )


def test_node_limit_rejects_agent_bound_node(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError,
        match="Agent-bound Node question_comprehension_info\\.review_keywords cannot have a Node limit",
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("pi-default", 2)],
            bindings=[binding("review_keywords", "pi-default")],
            node_limits=[node_limit("review_keywords", 1)],
        )


def test_node_limit_must_not_exceed_workspace_allocation(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError,
        match="Node limit for question_comprehension_info\\.fetch_questions exceeds Workspace allocation for code-default",
    ):
        validate_workspace_executor_configuration(
            workflow=context.workflow,
            executor_definitions=context.executors,
            allocations=[allocation("code-default", 2)],
            bindings=[binding("fetch_questions", "code-default")],
            node_limits=[node_limit("fetch_questions", 3)],
        )


def test_unbound_node_is_valid(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    validate_workspace_executor_configuration(
        workflow=context.workflow,
        executor_definitions=context.executors,
        allocations=[allocation("code-default", 4)],
        bindings=[binding("fetch_questions", "code-default")],
        node_limits=[],
    )
