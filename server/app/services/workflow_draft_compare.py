from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from server.app.services.workflow_draft_compare_conditions import (
    condition_identity as _condition_identity,
)
from server.app.services.workflow_draft_compare_conditions import (
    format_condition as _format_condition,
)
from server.app.services.workflow_draft_compare_metadata import (
    diff_metadata as _diff_metadata,
)
from server.app.services.workflow_draft_compare_support import (
    compute_risk_level,
    higher_risk,
    yaml_error_to_dict,
)
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.services.workflow_revision_change import structural_revision_changed
from server.app.workflows.definition import WorkflowDefinitionError, workflow_definition_from_dict
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowIntakeMode,
    WorkflowNode,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def _edge_identity(edge: WorkflowEdge, use_full: bool) -> str:
    if use_full:
        return f"{edge.source}|{edge.target}|{_condition_identity(edge.condition)}"
    return f"{edge.source}|{edge.target}"


def _detect_duplicate_source_target(edges: list[WorkflowEdge]) -> bool:
    seen: set[str] = set()
    for edge in edges:
        key = f"{edge.source}|{edge.target}"
        if key in seen:
            return True
        seen.add(key)
    return False


def _should_use_full_edge_identity(base: WorkflowDefinition, draft: WorkflowDefinition) -> bool:
    return _detect_duplicate_source_target(base.edges) or _detect_duplicate_source_target(
        draft.edges
    )


def _node_change_fields(base: WorkflowNode, draft: WorkflowNode) -> list[str]:
    fields: list[str] = []
    if base.label != draft.label:
        fields.append("label")
    if base.capability != draft.capability:
        fields.append("capability")
    if base.inputs != draft.inputs:
        fields.append("inputs")
    if base.outputs != draft.outputs:
        fields.append("outputs")
    if base.execution != draft.execution:
        fields.append("execution")
    base_terminal = base.terminal.outcome if base.terminal else None
    draft_terminal = draft.terminal.outcome if draft.terminal else None
    if base_terminal != draft_terminal:
        fields.append("terminal")
    return fields


def _node_field_risks(base: WorkflowNode, draft: WorkflowNode) -> dict[str, str]:
    risks: dict[str, str] = {}
    if base.label != draft.label:
        risks["label"] = "info"
    if base.capability != draft.capability:
        risks["capability"] = "breaking"

    base_inputs = set(base.inputs)
    draft_inputs = set(draft.inputs)
    if base.inputs != draft.inputs:
        if draft_inputs - base_inputs:
            risks["inputs"] = "warning"
        else:
            risks["inputs"] = "info"

    base_outputs = set(base.outputs)
    draft_outputs = set(draft.outputs)
    if base.outputs != draft.outputs:
        if base_outputs - draft_outputs:
            risks["outputs"] = "breaking"
        else:
            risks["outputs"] = "info"
    if base.execution != draft.execution:
        risks["execution"] = "warning"

    base_terminal = base.terminal.outcome if base.terminal else None
    draft_terminal = draft.terminal.outcome if draft.terminal else None
    if base_terminal != draft_terminal:
        risks["terminal"] = "breaking"

    return risks


def _aggregate_node_risk(field_risks: dict[str, str]) -> str:
    risk = "none"
    for field_risk in field_risks.values():
        risk = higher_risk(risk, field_risk)
    return risk


def _diff_nodes(
    base: WorkflowDefinition,
    draft: WorkflowDefinition,
    node_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> None:
    base_nodes = base.nodes
    draft_nodes = draft.nodes
    all_keys = set(base_nodes) | set(draft_nodes)

    for key in sorted(all_keys):
        base_node = base_nodes.get(key)
        draft_node = draft_nodes.get(key)

        if base_node is None and draft_node is not None:
            node_changes.append(
                {
                    "type": "added",
                    "node_key": key,
                    "label": draft_node.label,
                    "fields": [],
                    "risk": "info",
                }
            )
            risk_flags.append(
                {
                    "code": "node_added",
                    "severity": "info",
                    "message": f"新增节点 {key}。",
                }
            )
            continue

        if base_node is not None and draft_node is None:
            node_changes.append(
                {
                    "type": "removed",
                    "node_key": key,
                    "label": base_node.label,
                    "fields": [],
                    "risk": "breaking",
                }
            )
            risk_flags.append(
                {
                    "code": "node_removed",
                    "severity": "breaking",
                    "message": f"节点 {key} 被删除，下游依赖可能中断。",
                }
            )
            continue

        if base_node is not None and draft_node is not None:
            fields = _node_change_fields(base_node, draft_node)
            if not fields:
                continue
            field_risks = _node_field_risks(base_node, draft_node)
            risk = _aggregate_node_risk(field_risks)
            node_changes.append(
                {
                    "type": "modified",
                    "node_key": key,
                    "label": draft_node.label,
                    "fields": fields,
                    "risk": risk,
                }
            )

            if base_node.capability != draft_node.capability:
                risk_flags.append(
                    {
                        "code": "capability_changed",
                        "severity": "breaking",
                        "message": (
                            f"节点 {key} 的能力从 {base_node.capability} "
                            f"改为 {draft_node.capability}，执行器绑定可能失效。"
                        ),
                    }
                )

            base_outputs = set(base_node.outputs)
            draft_outputs = set(draft_node.outputs)
            removed_outputs = base_outputs - draft_outputs
            for output in sorted(removed_outputs):
                risk_flags.append(
                    {
                        "code": "output_removed",
                        "severity": "breaking",
                        "message": f"节点 {key} 的输出 {output} 被删除。",
                    }
                )

            added_inputs = set(draft_node.inputs) - set(base_node.inputs)
            for input_field in sorted(added_inputs):
                risk_flags.append(
                    {
                        "code": "input_added",
                        "severity": "warning",
                        "message": f"节点 {key} 新增输入 {input_field}。",
                    }
                )

            base_terminal = base_node.terminal.outcome if base_node.terminal else None
            draft_terminal = draft_node.terminal.outcome if draft_node.terminal else None
            if base_terminal != draft_terminal:
                risk_flags.append(
                    {
                        "code": "terminal_changed",
                        "severity": "breaking",
                        "message": (
                            f"节点 {key} 的终端结果从 {base_terminal} 改为 {draft_terminal}。"
                        ),
                    }
                )

            if base_node.label != draft_node.label and "capability" not in field_risks:
                risk_flags.append(
                    {
                        "code": "label_changed",
                        "severity": "info",
                        "message": f"节点 {key} 的显示名称从 {base_node.label} 改为 {draft_node.label}。",
                    }
                )


def _diff_edges(
    base: WorkflowDefinition,
    draft: WorkflowDefinition,
    edge_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> None:
    use_full = _should_use_full_edge_identity(base, draft)
    base_edges = {_edge_identity(edge, use_full): edge for edge in base.edges}
    draft_edges = {_edge_identity(edge, use_full): edge for edge in draft.edges}
    all_keys = set(base_edges) | set(draft_edges)

    for key in sorted(all_keys):
        base_edge = base_edges.get(key)
        draft_edge = draft_edges.get(key)

        if base_edge is None and draft_edge is not None:
            edge_changes.append(
                {
                    "type": "added",
                    "source": draft_edge.source,
                    "target": draft_edge.target,
                    "before_condition": None,
                    "after_condition": _format_condition(draft_edge.condition),
                    "risk": "warning",
                }
            )
            risk_flags.append(
                {
                    "code": "edge_added",
                    "severity": "warning",
                    "message": f"新增边 {draft_edge.source} -> {draft_edge.target}。",
                }
            )
            continue

        if base_edge is not None and draft_edge is None:
            edge_changes.append(
                {
                    "type": "removed",
                    "source": base_edge.source,
                    "target": base_edge.target,
                    "before_condition": _format_condition(base_edge.condition),
                    "after_condition": None,
                    "risk": "breaking",
                }
            )
            risk_flags.append(
                {
                    "code": "edge_removed",
                    "severity": "breaking",
                    "message": f"边 {base_edge.source} -> {base_edge.target} 被删除。",
                }
            )
            continue

        if base_edge is not None and draft_edge is not None and not use_full:
            base_condition = _format_condition(base_edge.condition)
            draft_condition = _format_condition(draft_edge.condition)
            if base_condition != draft_condition:
                edge_changes.append(
                    {
                        "type": "condition_changed",
                        "source": base_edge.source,
                        "target": base_edge.target,
                        "before_condition": base_condition,
                        "after_condition": draft_condition,
                        "risk": "breaking",
                    }
                )
                risk_flags.append(
                    {
                        "code": "edge_condition_changed",
                        "severity": "breaking",
                        "message": "分支条件变化会改变运行路径。",
                    }
                )


def _mode_fields_changed(base: WorkflowIntakeMode, draft: WorkflowIntakeMode) -> list[str]:
    fields: list[str] = []
    if base.label != draft.label:
        fields.append("label")
    if base.input_field != draft.input_field:
        fields.append("input_field")
    return fields


def _diff_intake(
    base: WorkflowDefinition,
    draft: WorkflowDefinition,
    intake_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> None:
    base_modes = base.intake.modes
    draft_modes = draft.intake.modes
    all_keys = set(base_modes) | set(draft_modes)

    for key in sorted(all_keys):
        base_mode = base_modes.get(key)
        draft_mode = draft_modes.get(key)

        if base_mode is None and draft_mode is not None:
            intake_changes.append(
                {
                    "type": "field_added",
                    "mode_key": key,
                    "field_key": key,
                    "risk": "warning",
                }
            )
            risk_flags.append(
                {
                    "code": "intake_field_added",
                    "severity": "warning",
                    "message": f"新增 intake 字段 {key}。",
                }
            )
            continue

        if base_mode is not None and draft_mode is None:
            intake_changes.append(
                {
                    "type": "field_removed",
                    "mode_key": key,
                    "field_key": key,
                    "risk": "info",
                }
            )
            risk_flags.append(
                {
                    "code": "intake_field_removed",
                    "severity": "info",
                    "message": f"intake 字段 {key} 被删除。",
                }
            )
            continue

        if base_mode is not None and draft_mode is not None:
            changed = _mode_fields_changed(base_mode, draft_mode)
            if changed:
                intake_changes.append(
                    {
                        "type": "mode_changed",
                        "mode_key": key,
                        "field_key": None,
                        "risk": "warning",
                    }
                )
                risk_flags.append(
                    {
                        "code": "intake_mode_changed",
                        "severity": "warning",
                        "message": f"intake 模式 {key} 配置发生变化。",
                    }
                )


def _invalid_compare(error: dict[str, Any]) -> dict[str, Any]:
    return {
        "valid": False,
        "base_revision": None,
        "draft_workflow": None,
        "summary": None,
        "errors": [error],
    }


# Explains the no-baseline preview mode inside every such compare result.
_NO_BASELINE_FLAG = {
    "code": "no_baseline",
    "severity": "info",
    "message": "该 workflow 从未发布：与空基线对比，展示草稿全貌（全部节点均为新增）。",
}


def _base_revision_summary(revision: dict[str, Any] | None) -> dict[str, Any] | None:
    if revision is None:
        return None
    return {
        "id": revision["id"],
        "version": revision["version"],
        "workflow_key": revision["workflow_key"],
        "definition_hash": revision["definition_hash"],
    }


def compare_workflow_draft(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    *,
    allow_missing_baseline: bool = False,
) -> dict[str, Any]:
    try:
        draft = workflow_definition_from_yaml_string(definition_yaml)
    except WorkflowDefinitionError as exc:
        # YAML parse failures arrive wrapped (see workflow_drafts); keep the
        # yaml error category so callers can distinguish syntax vs schema issues.
        if isinstance(exc.__cause__, yaml.YAMLError):
            return _invalid_compare(yaml_error_to_dict(exc.__cause__))
        return _invalid_compare({"category": "schema", "message": str(exc)})
    except Exception as exc:
        return _invalid_compare({"category": "schema", "message": str(exc)})

    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        return _invalid_compare(
            {"category": "revision", "message": f"Workspace {workspace_id} not found"}
        )

    default_workflow_key = str(workspace.get("default_workflow_key") or "")
    # An empty default key marks a blank-canvas workspace (schema v50): its
    # first publish adopts the draft key, so no mismatch is possible yet.
    if default_workflow_key and draft.key != default_workflow_key:
        return _invalid_compare(
            {
                "category": "schema",
                "message": (
                    f"Draft workflow key '{draft.key}' does not match "
                    f"workspace default workflow key '{default_workflow_key}'"
                ),
            }
        )

    revision = job_db.get_active_workflow_revision(workspace_id, draft.key)
    if revision is None and not allow_missing_baseline:
        return _invalid_compare(
            {"category": "revision", "message": f"No active workflow revision for {draft.key}"}
        )

    if revision is None:
        # No-baseline preview (studio-agent from-scratch authoring): diff the
        # draft against an empty base so a never-published workflow shows its
        # full shape (every node/edge/intake field reported as added) instead
        # of failing with a revision error.
        base = WorkflowDefinition(
            key=draft.key,
            label=draft.label,
            intake=WorkflowIntake(),
            nodes={},
            schema_version=draft.schema_version,
        )
    else:
        try:
            base = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        except Exception as exc:
            return _invalid_compare(
                {"category": "schema", "message": f"Failed to parse active revision: {exc}"}
            )

    node_changes: list[dict[str, Any]] = []
    edge_changes: list[dict[str, Any]] = []
    intake_changes: list[dict[str, Any]] = []
    metadata_changes: list[dict[str, Any]] = []
    risk_flags: list[dict[str, Any]] = []

    _diff_nodes(base, draft, node_changes, risk_flags)
    _diff_edges(base, draft, edge_changes, risk_flags)
    _diff_intake(base, draft, intake_changes, risk_flags)
    _diff_metadata(base, draft, metadata_changes, risk_flags)
    if revision is None:
        risk_flags.append(dict(_NO_BASELINE_FLAG))

    risk_level = compute_risk_level(
        node_changes, edge_changes, intake_changes, risk_flags, metadata_changes
    )
    creates_revision = structural_revision_changed(
        node_changes, edge_changes, intake_changes, metadata_changes
    )

    return {
        "valid": True,
        "creates_revision": creates_revision,
        "base_revision": _base_revision_summary(revision),
        "draft_workflow": {
            "key": draft.key,
            "label": draft.label,
            "version": int(revision["version"]) if revision is not None else 0,
        },
        "summary": {
            "risk_level": risk_level,
            "node_changes": node_changes,
            "edge_changes": edge_changes,
            "intake_changes": intake_changes,
            "metadata_changes": metadata_changes,
            "risk_flags": risk_flags,
        },
        "errors": [],
    }
