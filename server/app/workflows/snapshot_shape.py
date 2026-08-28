"""Shape validation for persisted workflow-definition snapshots.

Split from the schema module (#243 P1): structurally corrupt snapshots must
raise WorkflowDefinitionError — the error the scan path degrades on
per-workspace — not AttributeError/TypeError killing worker startup.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.schema import WorkflowDefinitionError

__test__ = False


def snapshot_field(
    payload: dict[str, Any], field_name: str, *, default: Any, list_form: bool
) -> Any:
    """Validate a persisted snapshot field's shape (#243 P1): corrupt shapes
    (``nodes`` as a list, null entries) must raise WorkflowDefinitionError —
    the error the scan path degrades on per-workspace — not AttributeError /
    TypeError killing worker startup. list_form=False wants a mapping of
    mappings (nodes); True wants a list of mappings (edges)."""
    value = payload.get(field_name)
    if value is None:
        return default
    if list_form:
        ok = isinstance(value, list) and all(isinstance(e, dict) for e in value)
    else:
        ok = isinstance(value, dict) and all(isinstance(e, dict) for e in value.values())
    if not ok:
        shape = "a list of mappings" if list_form else "a mapping of mappings"
        raise WorkflowDefinitionError(
            f"Workflow definition snapshot '{field_name}' must be {shape}"
        )
    return value


def ensure_mapping(value: Any, where: str) -> dict[str, Any] | None:
    """None passes through; a mapping passes through; anything else is a
    corrupt snapshot shape and raises (same degradation path as above)."""
    if not isinstance(value, dict | None):
        raise WorkflowDefinitionError(f"{where} must be a mapping")
    return value
