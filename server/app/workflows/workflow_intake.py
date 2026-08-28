"""Workflow intake block loading (split from loader.py for file budget).

Same split precedent as start_node.py: one self-contained definition block,
its validation, and its schema types, so the loader stays the orchestration
spine.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.schema import (
    WorkflowDefinitionError,
    WorkflowIntake,
    WorkflowIntakeMode,
)


def load_intake(raw: dict[str, Any]) -> WorkflowIntake:
    raw_intake = raw.get("intake", {})
    if raw_intake is None:
        raw_intake = {}
    if not isinstance(raw_intake, dict):
        raise WorkflowDefinitionError("Workflow intake must be a mapping")
    raw_modes = raw_intake.get("modes", {})
    if raw_modes is None:
        raw_modes = {}
    if not isinstance(raw_modes, dict):
        raise WorkflowDefinitionError("Workflow intake.modes must be a mapping")

    modes: dict[str, WorkflowIntakeMode] = {}
    for mode_key, raw_mode in raw_modes.items():
        if not isinstance(mode_key, str) or not mode_key:
            raise WorkflowDefinitionError("Intake mode keys must be non-empty strings")
        if not isinstance(raw_mode, dict):
            raise WorkflowDefinitionError(f"Intake mode {mode_key} must be a mapping")
        label = raw_mode.get("label", mode_key)
        input_field = raw_mode.get("input_field", mode_key)
        if not isinstance(label, str) or not label:
            raise WorkflowDefinitionError(f"Intake mode {mode_key}.label must be a string")
        if not isinstance(input_field, str) or not input_field:
            raise WorkflowDefinitionError(f"Intake mode {mode_key}.input_field must be a string")
        modes[mode_key] = WorkflowIntakeMode(
            key=mode_key,
            label=label,
            input_field=input_field,
        )
    return WorkflowIntake(modes=modes)
