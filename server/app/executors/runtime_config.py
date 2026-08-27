"""Backward-compatible re-exports of the ``executor_runtime`` config models.

The models moved to the neutral configuration package (issue #188: settings
must not depend on executors); this module keeps the historical import path
``server.app.executors.runtime_config`` working for the executor-side and
test consumers.
"""

from __future__ import annotations

from server.app.configuration.executor_runtime import (
    AgentWorkersRuntimeConfig as AgentWorkersRuntimeConfig,
)
from server.app.configuration.executor_runtime import (
    ExecutorRuntimeConfig as ExecutorRuntimeConfig,
)
from server.app.configuration.executor_runtime import (
    OpenClawRuntimeConfig as OpenClawRuntimeConfig,
)
from server.app.configuration.executor_runtime import (
    StartupValidationError as StartupValidationError,
)
from server.app.configuration.executor_runtime import (
    WorkflowsRuntimeConfig as WorkflowsRuntimeConfig,
)
from server.app.configuration.executor_runtime import (
    validate_runtime as validate_runtime,
)
