"""Node-declared ``config_schema:`` blocks and reserved execution keys (P-0.5).

A workflow node may declare a ``config_schema:`` block (typed subset,
``server.app.config_schema``) describing its tunable, non-secret parameters.
The declaration versions with the revision snapshot and freezes at intake,
joining the schema-resolution chain between Agent definitions and the
executor capability fallback (spec D15).

``timeout_seconds`` / ``sandbox_network`` are platform-reserved execution
keys: the platform merges them into every code-routed node's effective
schema (``server.app.services.node_execution_config``), so nodes set them
via ``config:`` or workspace overrides without declaring them — a node
``config_schema`` redeclaring them is rejected here.
"""

from __future__ import annotations

from typing import Any

from server.app.config_schema import ConfigSchemaError, validate_config_schema
from server.app.workflows.schema import WorkflowDefinitionError

RESERVED_EXECUTION_KEYS = frozenset({"timeout_seconds", "sandbox_network"})


def load_node_config_schema(raw_node: dict[str, Any], node_key: str) -> dict[str, Any]:
    """Parse and validate a node's optional ``config_schema:`` block."""
    raw = raw_node.get("config_schema")
    if raw is None:
        return {}
    try:
        validate_config_schema(raw)
    except ConfigSchemaError as exc:
        raise WorkflowDefinitionError(f"Node {node_key}.config_schema: {exc}") from exc
    overlap = RESERVED_EXECUTION_KEYS & set(raw.get("properties") or {})
    if overlap:
        raise WorkflowDefinitionError(
            f"Node {node_key}.config_schema must not redeclare platform-reserved"
            f" execution keys: {sorted(overlap)}"
        )
    return dict(raw)
