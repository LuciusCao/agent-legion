"""Raw executor-definition loading on top of the kind registry.

Kept out of ``executors.config`` (which ``executors.runtime_config`` imports)
and out of ``executors.kinds`` (which imports ``runtime_config``): placing the
loader in either would close an import cycle between the two.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ValidationError
from pydantic_core import InitErrorDetails

from server.app.executors.kinds import load_executor_config


def _validation_error_with_executor_id(exc: ValidationError, executor_id: str) -> ValidationError:
    line_errors = exc.errors(include_url=False)
    for error in line_errors:
        ctx = error.get("ctx") or {}
        ctx["executor_id"] = executor_id
        error["ctx"] = ctx
        error["loc"] = (executor_id, *error.get("loc", ()))
    return ValidationError.from_exception_data(exc.title, cast(list[InitErrorDetails], line_errors))


def load_executor_definitions(raw: dict[str, object]) -> dict[str, BaseModel]:
    """Validate a mapping of executor ID to executor configuration.

    Dispatch goes through the kind registry: unknown kinds raise
    ``UnknownExecutorKindError`` (message includes the executor ID); model
    validation errors are wrapped with the executor ID context.
    """
    definitions: dict[str, BaseModel] = {}
    for executor_id, value in raw.items():
        if not isinstance(value, dict):
            raise TypeError(
                f"Executor {executor_id!r}: expected a mapping, got {type(value).__name__}"
            )
        try:
            definitions[executor_id] = load_executor_config(executor_id, value)
        except ValidationError as exc:
            raise _validation_error_with_executor_id(exc, executor_id) from exc
    return definitions
