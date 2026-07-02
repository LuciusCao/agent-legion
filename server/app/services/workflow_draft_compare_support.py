"""Shared helpers for workflow draft comparison."""

from typing import Any

import yaml

RISK_ORDER = {"none": 0, "info": 1, "warning": 2, "breaking": 3}


def higher_risk(a: str, b: str) -> str:
    return a if RISK_ORDER.get(a, 0) >= RISK_ORDER.get(b, 0) else b


def compute_risk_level(
    node_changes: list[dict[str, Any]],
    edge_changes: list[dict[str, Any]],
    intake_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> str:
    risk = "none"
    for change in node_changes + edge_changes + intake_changes:
        risk = higher_risk(risk, change["risk"])
    for flag in risk_flags:
        risk = higher_risk(risk, flag["severity"])
    return risk


def yaml_error_to_dict(exc: yaml.YAMLError) -> dict[str, Any]:
    mark = getattr(exc, "problem_mark", None)
    return {
        "category": "yaml",
        "message": str(exc.problem) if hasattr(exc, "problem") and exc.problem else str(exc),
        "line": mark.line + 1 if mark is not None else None,
        "column": mark.column + 1 if mark is not None else None,
    }
