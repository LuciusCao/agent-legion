from __future__ import annotations

from collections.abc import Container
from typing import Any


def execution_control_rejects_node(
    *,
    node_key: str,
    execution_mode: str,
    target_node_key: str | None,
    allowed_node_keys: Container[str] | None,
    current_control: dict[str, Any],
) -> bool:
    if current_control["execution_paused"]:
        return True
    if current_control["execution_mode"] != execution_mode:
        return True
    if execution_mode == "full":
        return False
    if execution_mode == "until_node":
        if current_control["execution_mode"] != "until_node":
            return True
        if current_control["target_node_key"] != target_node_key:
            return True
        return allowed_node_keys is not None and node_key not in allowed_node_keys
    return True
