"""Mutable runtime settings for an otherwise immutable workflow revision."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from server.app.jobs.queries.upgrade_impl_identity import (
    acquire_implementation_publication_lock,
)
from server.app.services.agent_profile_provenance import (
    embed_provenance,
    provenance_from_revision_json,
)
from server.app.services.workflow_revision_change import revision_structurally_changed
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def embed_node_code_pins(definition_json: str, pins: dict) -> str:
    """definition_json with the pins snapshot embedded alongside it (EXEC-CODE-002).

    node_code_pins are publish-moment state, not part of the workflow
    definition: they ride inside the stored definition_json but stay out of
    definition_hash (which covers the pure definition). Publish and the
    runtime-only in-place edit both embed through here (#287).
    """
    if not pins:
        return definition_json
    payload = json.loads(definition_json)
    payload["node_code_pins"] = pins
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save_revision_runtime_or_publish(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    publish: Callable[[str, WorkflowDefinition], dict],
) -> dict:
    active = job_db.get_active_workflow_revision(workspace_id, definition.key)
    if active is None:
        return publish(workspace_id, definition)
    current = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    # #1114：与草稿对比的 creates_revision 同一判定函数（runtime 属结构）。
    if revision_structurally_changed(current, definition):
        return publish(workspace_id, definition)
    definition_json = serialize_definition(definition)
    # Runtime-only updates must not drop the publish-time node_code_pins
    # snapshot (EXEC-CODE-002): carry it over from the stored payload. The
    # hash still covers the pure definition only (same rule as publish).
    new_hash = definition_hash(definition_json)
    current_pins = json.loads(str(active["definition_json"])).get("node_code_pins")
    definition_json = embed_node_code_pins(definition_json, current_pins or {})
    # #935：结构未变 = 档案字段未变，v93 provenance 原样保留（同 pins）。
    definition_json = embed_provenance(
        definition_json, provenance_from_revision_json(str(active["definition_json"]))
    )
    with job_db.connect() as conn:
        # #759 P2-A：runtime-only 原地编辑改写 active revision 的
        # definition_json/definition_hash，同属 implementation-publication
        # 锁域（与发布同事务锁）——否则 upgrade guard 重读到提交之间可被
        # 原地编辑穿插，upgrade pin 到已被改写的 revision 内容。
        acquire_implementation_publication_lock(conn, workspace_id)
        row = conn.execute(
            "update workflow_revisions set definition_json=%s, definition_hash=%s"
            " where id=%s returning *",
            (definition_json, new_hash, active["id"]),
        ).fetchone()
    if row is None:
        raise ValueError("workflow revision not found")
    return dict(row)
