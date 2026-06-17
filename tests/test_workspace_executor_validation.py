from __future__ import annotations

from dataclasses import dataclass

import pytest

from server.app.executors.config import (
    LocalCapabilityConfig,
    LocalExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.pipelines.definition import (
    PipelineDefinition,
    PipelineIntake,
    PipelineNode,
)
from server.app.services.job_errors import InvalidOperationError


@dataclass
class ValidationContext:
    pipeline: PipelineDefinition
    executors: dict[str, LocalExecutorConfig | PiExecutorConfig]


@pytest.fixture
def context() -> ValidationContext:
    pipeline = PipelineDefinition(
        key="reading_analysis",
        label="Reading Analysis",
        intake=PipelineIntake(),
        nodes={
            "fetch_questions": PipelineNode(
                key="fetch_questions",
                label="Fetch Questions",
                capability="fetch_questions",
            ),
            "review_keywords": PipelineNode(
                key="review_keywords",
                label="Review Keywords",
                capability="review_keywords",
            ),
        },
    )
    executors: dict[str, LocalExecutorConfig | PiExecutorConfig] = {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=4,
            capabilities={
                "fetch_questions": LocalCapabilityConfig(handler="fetch_questions_handler"),
            },
        ),
        "pi-default": PiExecutorConfig(
            kind="pi",
            global_capacity=2,
            capabilities={
                "review_keywords": PiCapabilityConfig(skill="reading_analysis/review_keywords"),
            },
        ),
    }
    return ValidationContext(pipeline=pipeline, executors=executors)


def allocation(executor_id: str, concurrency_limit: int) -> dict[str, object]:
    return {"executor_id": executor_id, "concurrency_limit": concurrency_limit}


def binding(
    node_key: str, executor_id: str, workflow_key: str = "reading_analysis"
) -> dict[str, object]:
    return {"workflow_key": workflow_key, "node_key": node_key, "executor_id": executor_id}


def node_limit(
    node_key: str, concurrency_limit: int, workflow_key: str = "reading_analysis"
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

    with pytest.raises(InvalidOperationError, match="Duplicate Executor allocation local-default"):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 2), allocation("local-default", 3)],
            bindings=[],
            node_limits=[],
        )


def test_unknown_executor_ids_are_rejected(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(InvalidOperationError, match="Unknown Executor unknown-exec"):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
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
        InvalidOperationError, match="Workspace limit 5 exceeds local-default global capacity 4"
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 5)],
            bindings=[],
            node_limits=[],
        )


def test_duplicate_node_bindings_are_rejected(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Duplicate Node binding reading_analysis\\.fetch_questions"
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 4)],
            bindings=[
                binding("fetch_questions", "local-default"),
                binding("fetch_questions", "local-default"),
            ],
            node_limits=[],
        )


def test_binding_pipeline_must_equal_workspace_pipeline(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Unknown Pipeline Node other_pipeline\\.fetch_questions"
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 4)],
            bindings=[binding("fetch_questions", "local-default", workflow_key="other_pipeline")],
            node_limits=[],
        )


def test_binding_node_must_exist_in_pipeline(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Unknown Pipeline Node reading_analysis\\.unknown_node"
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 4)],
            bindings=[binding("unknown_node", "local-default")],
            node_limits=[],
        )


def test_binding_executor_must_be_allocated(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Executor local-default is not allocated to this Workspace"
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[],
            bindings=[binding("fetch_questions", "local-default")],
            node_limits=[],
        )


def test_binding_rejects_executor_without_capability(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(InvalidOperationError, match="does not support capability review_keywords"):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 4)],
            bindings=[binding("review_keywords", "local-default")],
            node_limits=[],
        )


def test_duplicate_node_limits_are_rejected(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError, match="Duplicate Node limit reading_analysis\\.fetch_questions"
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 4)],
            bindings=[binding("fetch_questions", "local-default")],
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
        match="Node limit requires binding for reading_analysis\\.fetch_questions",
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 4)],
            bindings=[],
            node_limits=[node_limit("fetch_questions", 1)],
        )


def test_node_limit_rejects_agent_bound_node(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    with pytest.raises(
        InvalidOperationError,
        match="Agent-bound Node reading_analysis\\.review_keywords cannot have a Node limit",
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
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
        match="Node limit for reading_analysis\\.fetch_questions exceeds Workspace allocation for local-default",
    ):
        validate_workspace_executor_configuration(
            pipeline=context.pipeline,
            executor_definitions=context.executors,
            allocations=[allocation("local-default", 2)],
            bindings=[binding("fetch_questions", "local-default")],
            node_limits=[node_limit("fetch_questions", 3)],
        )


def test_unbound_node_is_valid(context: ValidationContext) -> None:
    from server.app.services.workspace_executor_validation import (
        validate_workspace_executor_configuration,
    )

    validate_workspace_executor_configuration(
        pipeline=context.pipeline,
        executor_definitions=context.executors,
        allocations=[allocation("local-default", 4)],
        bindings=[binding("fetch_questions", "local-default")],
        node_limits=[],
    )
