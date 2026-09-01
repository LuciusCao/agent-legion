"""Default node instructions for Agent-routed workflow nodes.

The Agent run prompt is a fixed platform envelope (job/skill paths, declared
inputs/outputs, output discipline) plus one node-instructions section. The
node's ``execution.prompt`` selects that section: empty means the
auto-assembled default built here; a non-empty value replaces the default
wholesale — it is never appended to it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["build_default_node_instructions", "build_node_instructions"]


def build_node_instructions(manifest: Mapping[str, Any]) -> str:
    """The node-instructions section of the run prompt for one manifest.

    The node's ``execution.prompt`` (manifest key ``additional_prompt``)
    selects the section: a non-empty custom prompt REPLACES the
    auto-assembled default wholesale — it is never appended as
    "Additional node instructions" anymore; an empty prompt selects the
    default. ``node_label`` feeds the default; legacy manifests without it
    fall back to the node key.
    """
    custom = str(manifest.get("additional_prompt", "")).strip()
    if custom:
        return custom
    return build_default_node_instructions(
        node_key=str(manifest["node_key"]),
        label=str(manifest.get("node_label") or manifest["node_key"]),
        capability=str(manifest.get("capability") or ""),
        skill=str(manifest.get("skill") or ""),
        inputs=[str(item) for item in manifest["inputs"]],
        expected_outputs=[str(item) for item in manifest["expected_outputs"]],
    )


def _names(items: Sequence[str]) -> str:
    return ", ".join(f"`{item}`" for item in items)


def build_default_node_instructions(
    *,
    node_key: str,
    label: str,
    capability: str,
    skill: str,
    inputs: Sequence[str],
    expected_outputs: Sequence[str],
) -> str:
    """Auto-assembled default instructions for one Agent node (English, concise).

    References the node's own contract by name: the label/capability say what
    the task is, the skill key points at the loaded skill, and the declared
    inputs/outputs spell out which files to read and which files must exist
    when the run finishes.
    """
    skill_clause = (
        f"Use the loaded `{skill}` skill and follow its instructions and output contract."
        if skill
        else "Use the loaded node skill and follow its instructions and output contract."
    )
    input_clause = (
        f"Read the declared input files ({_names(inputs)}) from the working directory."
        if inputs
        else "This node declares no inputs."
    )
    output_clause = (
        f"Produce the required output files ({_names(expected_outputs)}): "
        "the run only passes validation when every declared output exists in "
        "the working directory."
    )
    return (
        f"Your task: {label or node_key} (capability `{capability}`). "
        f"{skill_clause} {input_clause} {output_clause}"
    )
