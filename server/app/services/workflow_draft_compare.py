from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from server.app.services.workflow_draft_compare_edges import (
    _detect_duplicate_source_target,
    _edge_identity,
    _should_use_full_edge_identity,
)
from server.app.services.workflow_draft_compare_edges import (
    diff_edges as _diff_edges,
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
    WorkflowIntake,
    WorkflowIntakeMode,
    WorkflowNode,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

# #431: the edge diff (identity set + order fingerprint) lives in
# workflow_draft_compare_edges; the underscore helpers stay importable from
# here for the existing sibling/test import sites.
__all__ = [
    "_detect_duplicate_source_target",
    "_edge_identity",
    "_should_use_full_edge_identity",
    "compare_workflow_draft",
    "_node_change_fields",
    "_node_field_risks",
]


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
    if base.skill != draft.skill:
        fields.append("skill")
    if _normalized_config(base) != _normalized_config(draft):
        fields.append("config")
    if _normalized_config_schema(base) != _normalized_config_schema(draft):
        fields.append("config_schema")
    base_terminal = base.terminal.outcome if base.terminal else None
    draft_terminal = draft.terminal.outcome if draft.terminal else None
    if base_terminal != draft_terminal:
        fields.append("terminal")
    if base.accepted_item_types != draft.accepted_item_types:
        fields.append("accepted_item_types")
    # Issue #431: the remaining structural fields. Each of these version with
    # the revision via ``_structural_payload`` (asdict + ``==``), so the
    # compare must see them too or a same-set-different-order draft shows
    # "no changes" while publishing still bumps the version. ``after`` is a
    # plain ordered list — list equality is order-sensitive on purpose, the
    # same alignment as the publish path.
    if base.node_type != draft.node_type:
        fields.append("node_type")
    if base.after != draft.after:
        fields.append("after")
    if base.shard != draft.shard:
        fields.append("shard")
    if base.reduce != draft.reduce:
        fields.append("reduce")
    return fields


# Issue #418: ``config`` / ``config_schema`` are structural — they version with
# the revision (the publish path diffs them via ``_structural_payload``), so
# the compare must see them too or a config-only draft shows "no changes"
# while publishing still bumps the version. The loader already normalizes a
# missing/``None`` block to ``{}``; the compare cannot assume the model objects
# both came through the loader (``replace()`` can hand it ``None``), so the
# same normalization applies before the dict equality (which is already
# key-order independent).
def _normalized_config(node: WorkflowNode) -> dict[str, Any]:
    return node.config if node.config is not None else {}


def _normalized_config_schema(node: WorkflowNode) -> dict[str, Any]:
    return node.config_schema if node.config_schema is not None else {}


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
    # Rebinding the skill content changes what the Agent runs (issue #76):
    # structural like the DAG, but not a routing break — same tier as
    # execution overrides.
    if base.skill != draft.skill:
        risks["skill"] = "warning"
    # Tunable node settings (#418): structural (they version with the
    # revision) and they change what the node actually runs — same tier as
    # execution overrides, not a routing break.
    if _normalized_config(base) != _normalized_config(draft):
        risks["config"] = "warning"
    if _normalized_config_schema(base) != _normalized_config_schema(draft):
        risks["config_schema"] = "warning"

    base_terminal = base.terminal.outcome if base.terminal else None
    draft_terminal = draft.terminal.outcome if draft.terminal else None
    if base_terminal != draft_terminal:
        risks["terminal"] = "breaking"
    if base.accepted_item_types != draft.accepted_item_types:
        risks["accepted_item_types"] = "breaking"

    # Issue #431: the remaining structural fields keep compare and publish
    # aligned. A node_type switch (code→agent) changes what executes the
    # node, so it is a breaking change like capability; shard/reduce alter
    # the fan-out/fan-in execution shape (structural, but not a routing
    # break — same tier as execution overrides); an after reorder keeps the
    # same dependency set while changing the serialized structure.
    if base.node_type != draft.node_type:
        risks["node_type"] = "breaking"
    if base.after != draft.after:
        risks["after"] = "warning"
    if base.shard != draft.shard:
        risks["shard"] = "warning"
    if base.reduce != draft.reduce:
        risks["reduce"] = "warning"

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
                    "node_type": draft_node.node_type,
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
                    "node_type": base_node.node_type,
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
                    "node_type": draft_node.node_type,
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

            if base_node.accepted_item_types != draft_node.accepted_item_types:
                risk_flags.append(
                    {
                        "code": "accepted_item_types_changed",
                        "severity": "breaking",
                        "message": f"节点 {key} 的入口条目类型契约（accepted_item_types）发生变化。",
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
        "workspace_id": revision["workspace_id"],
        # #211 M2: the column is gone — the deprecated response field keeps
        # returning the identity value until the M3 contract drop.
        "workflow_key": revision["workspace_id"],
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
    except json.JSONDecodeError as exc:
        # #204: the parser's only other declared failure — a ``!include``
        # style embedded JSON payload that does not parse. Both arms are
        # user-authored input errors and belong in the compare report; a
        # genuine TypeError from the loader is a programming error and 500s.
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
        except (json.JSONDecodeError, WorkflowDefinitionError) as exc:
            # #204: both failure modes of parsing a stored revision are
            # data-shape errors (a truncated definition_json blob or a
            # definition that violates the schema — the loader's declared
            # error space, #243 hardened it to WorkflowDefinitionError for
            # exactly this degradation). Degrade to an invalid compare so
            # the studio surfaces it instead of a 500.
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
