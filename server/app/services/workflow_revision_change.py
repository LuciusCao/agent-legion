"""Classify whether a draft requires a new workflow revision."""

from typing import Any


def structural_revision_changed(
    node_changes: list[dict[str, Any]],
    edge_changes: list[dict[str, Any]],
    intake_changes: list[dict[str, Any]],
    metadata_changes: list[dict[str, Any]],
) -> bool:
    return bool(
        edge_changes
        or intake_changes
        or metadata_changes
        or any(
            change["type"] != "modified" or any(field != "execution" for field in change["fields"])
            for change in node_changes
        )
    )
