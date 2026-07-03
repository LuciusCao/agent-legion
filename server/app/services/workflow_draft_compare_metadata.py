from typing import Any

from server.app.workflows.schema import WorkflowDefinition


def diff_metadata(
    base: WorkflowDefinition,
    draft: WorkflowDefinition,
    metadata_changes: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
) -> None:
    if base.label != draft.label:
        metadata_changes.append(
            {
                "type": "modified",
                "field": "label",
                "before_value": base.label,
                "after_value": draft.label,
                "risk": "info",
            }
        )
        risk_flags.append(
            {
                "code": "workflow_label_changed",
                "severity": "info",
                "message": f"Workflow 显示名称从 {base.label} 改为 {draft.label}。",
            }
        )
    if base.schema_version != draft.schema_version:
        metadata_changes.append(
            {
                "type": "modified",
                "field": "schema_version",
                "before_value": str(base.schema_version),
                "after_value": str(draft.schema_version),
                "risk": "breaking",
            }
        )
        risk_flags.append(
            {
                "code": "schema_version_changed",
                "severity": "breaking",
                "message": (
                    f"Workflow schema_version 从 {base.schema_version} "
                    f"改为 {draft.schema_version}。"
                ),
            }
        )
