"""Executor primitives: lease models, the adapter contract, the code pool.

P-0.5: exactly one adapter exists (``executors.code``); the pi/openclaw
adapters and the kind-registration machinery are retired (schema v47).
"""

from server.app.executors.contracts import Executor
from server.app.executors.models import ExecutionContext, ExecutionResult

__all__ = ["ExecutionContext", "ExecutionResult", "Executor"]
