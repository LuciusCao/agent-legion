"""Validation and entity-key helpers for custom node code (EXEC-CODE-002).

Split from ``node_codes.py`` (#779 codex train review R2 P2 — the publish-side
re-validation pushed the service module past its file budget; the pure
validation/keying helpers move here unchanged). ``node_codes`` re-exports
these names, so existing imports keep working.
"""

from __future__ import annotations

import ast
import hashlib

from server.app.services.job_errors import InvalidOperationError

# Custom nodes stay single-file and cohesive; oversized code is rejected.
# The byte ceiling is instance-configurable (#628: heavy self-contained
# nodes outgrew the hardcode) — DEFAULT_MAX_CODE_BYTES mirrors the
# ``executor_runtime.workflows.node_code_max_bytes`` default
# (AGENT_LEGION_NODE_CODE_MAX_BYTES); both must stay in sync.
DEFAULT_MAX_CODE_BYTES = 64 * 1024

_ENTITY_KEY_SEPARATOR = ":"


def _entity_key(workflow_key: str, node_key: str) -> str:
    if _ENTITY_KEY_SEPARATOR in workflow_key:
        raise InvalidOperationError(
            f"workflow key must not contain {_ENTITY_KEY_SEPARATOR!r}: {workflow_key}"
        )
    return f"{workflow_key}{_ENTITY_KEY_SEPARATOR}{node_key}"


def _split_entity_key(entity_key: str) -> tuple[str, str]:
    workflow_key, _, node_key = entity_key.partition(_ENTITY_KEY_SEPARATOR)
    return workflow_key, node_key


def validate_node_code(code: str, max_code_bytes: int = DEFAULT_MAX_CODE_BYTES) -> None:
    """Syntax + module-level ``run`` + size contract for custom node code.

    ``max_code_bytes`` (#628): the instance-level byte budget, injected by
    callers that hold Settings (route layer passes
    ``settings.executor_runtime.workflows.node_code_max_bytes``); the module
    default is the unchanged 64KB, which keeps the historical behavior for
    non-DI constructions (workers/tests/seed paths).
    """
    if len(code.encode("utf-8")) > max_code_bytes:
        raise InvalidOperationError(
            f"node code exceeds the {max_code_bytes}-byte size limit"
            f" (node_code_max_bytes, default {DEFAULT_MAX_CODE_BYTES})"
        )
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise InvalidOperationError(f"node code is not valid Python: {exc}") from exc
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            return
    raise InvalidOperationError("node code must define a module-level 'run' function")


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()
