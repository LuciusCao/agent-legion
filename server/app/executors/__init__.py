"""Executor contracts.

Deliberately lightweight: adapter modules (local/pi/openclaw) are NOT imported
here — they pull in workflow-side runners while those runners import executor
primitives, so eager adapter imports closed an import cycle. Import
``server.app.executors.registration`` where kind registration is required.
"""

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.protocol import Executor

__all__ = ["ExecutionContext", "ExecutionResult", "Executor"]
