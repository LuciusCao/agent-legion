"""Runtime config channel (M47): save / history / rollback for runtime settings.

Studio 分离改造的后端地基：结构性改动（DAG、节点类型、config/config_schema、
skill、execution.runtime 换引擎等）仍走草稿 + 发布产生新 revision；runtime
字段（节点级 provider/model/thinking/prompt/prompt_mode/tools/requires_labels
与 workflow 顶层 ``execution`` 默认块的同类键——顶层不接受 prompt/prompt_mode，
runtime 键属结构）走本通道：原地更新 active revision（revision id/version 不变，
``save_revision_runtime_or_publish`` 的既有机制）并追加一条不可变版本行
（``workflow_runtime_config_versions``，schema v102）——每版本即审计行，
回滚 = 把目标版本的切片应用到当前结构后追加为新版本（目标版本不认识
的新增节点保留现值，故跨结构变更后回滚的结果不等于目标版本原文），
历史永不篡改。

解析链不变（EXEC-RUNTIME-DISPATCH-001）：dispatch/claim 永远读 active revision
的 definition_json，本模块的版本表不参与解析；在途 job 持 intake 冻结快照，
取新配置走既有「升级 workflow」路径（其 already_current 判定按 revision id +
definition_hash 比对——原地更新会改 hash，已冻结 job 不会被误判为 current）。

并发语义：写面挂在 implementation-publication 锁域（#759/#1243，与发布、
versioned_entities 的 Agent/node_code 生命周期同域）。锁内重读 active 行并以其
内容为 changed_keys 的 diff 基线，因此并发 runtime 保存在锁下串行、版本行的
config_json 逐行等于当时写入 active revision 的 runtime 切片。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.upgrade_impl_identity import (
    acquire_implementation_publication_lock,
)
from server.app.jobs.queries.workflow_runtime_config import (
    append_runtime_config_version_tx,
    update_active_revision_definition_tx,
)
from server.app.services.agent_profile_provenance import (
    embed_provenance,
    provenance_from_revision_json,
)
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.workflow_revision_change import revision_structurally_changed
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

# Node-level runtime keys (``execution.runtime`` excluded: it selects the
# execution-profile source and stays structural, #933); the top-level block
# accepts the same family minus prompt/prompt_mode (the loader rejects them
# there). ``tools`` / ``requires_labels`` ride the node payload, not
# ``execution``.
_NODE_EXECUTION_KEYS = ("provider", "model", "thinking", "prompt", "prompt_mode")
_TOP_LEVEL_EXECUTION_KEYS = ("provider", "model", "thinking")


def embed_node_code_pins(definition_json: str, pins: dict) -> str:
    """definition_json with the pins snapshot embedded alongside it (EXEC-CODE-002).

    node_code_pins are publish-moment state, not part of the workflow
    definition: they ride inside the stored definition_json but stay out of
    definition_hash (which covers the pure definition). Publish and the
    runtime-only in-place edit both embed through here (#287). (Moved here
    from workflow_revision_runtime with the in-place write path, M47.)
    """
    if not pins:
        return definition_json
    payload = json.loads(definition_json)
    payload["node_code_pins"] = pins
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def extract_runtime_config(definition: WorkflowDefinition) -> dict[str, Any]:
    """The runtime slice of a definition, fully normalized (empties included).

    Every node appears with the complete key set so a rollback knows what to
    CLEAR, not just what to set; serialization is canonical (sort_keys) so
    versions diff byte-identically.
    """
    top = definition.execution
    return {
        "execution": {key: getattr(top, key) for key in _TOP_LEVEL_EXECUTION_KEYS},
        "nodes": {
            key: {
                **{field: getattr(node.execution, field) for field in _NODE_EXECUTION_KEYS},
                "tools": list(node.tools),
                "requires_labels": dict(node.requires_labels),
            }
            for key, node in definition.nodes.items()
        },
    }


def apply_runtime_config(
    definition: WorkflowDefinition, config: dict[str, Any]
) -> WorkflowDefinition:
    """A new definition with the runtime slice replaced by ``config``.

    Nodes missing from ``config['nodes']`` keep their current values (a
    rollback target recorded before a structural publish may not know nodes
    added since); ``execution.runtime`` is never touched — it is structural
    and a runtime channel payload never carries it.
    """
    top = config.get("execution") or {}
    execution = dc_replace(
        definition.execution,
        **{key: str(top.get(key, "")) for key in _TOP_LEVEL_EXECUTION_KEYS},
    )
    node_configs = config.get("nodes") or {}
    nodes = {}
    for key, node in definition.nodes.items():
        node_config = node_configs.get(key)
        if node_config is None:
            nodes[key] = node
            continue
        nodes[key] = dc_replace(
            node,
            execution=dc_replace(
                node.execution,
                **{field: str(node_config.get(field, "")) for field in _NODE_EXECUTION_KEYS},
            ),
            tools=tuple(str(tool) for tool in node_config.get("tools") or ()),
            requires_labels={
                str(label): str(value)
                for label, value in (node_config.get("requires_labels") or {}).items()
            },
        )
    return dc_replace(definition, execution=execution, nodes=nodes)


def _flatten_runtime_config(config: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in config.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_runtime_config(value, path))
        else:
            flat[path] = value
    return flat


def diff_runtime_config_keys(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Sorted dotted paths whose flattened values differ (lists compare whole)."""
    old_flat = _flatten_runtime_config(old)
    new_flat = _flatten_runtime_config(new)
    return sorted(
        path for path in set(old_flat) | set(new_flat) if old_flat.get(path) != new_flat.get(path)
    )


def _serialize_config(payload: Any) -> str:
    """Canonical JSON for version payloads (config dict or changed-keys list)."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def update_active_revision_runtime_in_place(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    *,
    active: dict[str, Any],
    created_by: str,
    on_commit: Callable[[Any], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Runtime-only in-place edit of the active revision + version append.

    Returns ``(revision_row, version_row_or_None)`` — the version row is None
    when the runtime slice did not change (a no-change save stays a no-op).
    ``on_commit`` rides the same transaction (#1221: draft publish passes the
    draft-row removal). The active row's identity is revalidated under the
    implementation-publication lock (#1226 二轮 codex P1: a concurrent
    structural publish archiving the row aborts the edit with ConflictError —
    callers retry against the new active); the changed-keys diff base is the
    LOCKED re-read's content, so concurrent runtime saves serialize with
    accurate per-version diffs.
    """
    definition_json = serialize_definition(definition)
    new_hash = definition_hash(definition_json)
    # Runtime-only updates must not drop the publish-time node_code_pins
    # snapshot (EXEC-CODE-002): carry it over from the stored payload. The
    # hash still covers the pure definition only (same rule as publish).
    current_pins = json.loads(str(active["definition_json"])).get("node_code_pins")
    definition_json = embed_node_code_pins(definition_json, current_pins or {})
    # #935：结构未变 = 档案字段未变，v93 provenance 原样保留（同 pins）。
    definition_json = embed_provenance(
        definition_json, provenance_from_revision_json(str(active["definition_json"]))
    )
    new_config = extract_runtime_config(definition)
    with job_db.connect() as conn:
        # #759 P2-A：runtime-only 原地编辑改写 active revision 的
        # definition_json/definition_hash，同属 implementation-publication
        # 锁域（与发布同事务锁）——否则 upgrade guard 重读到提交之间可被
        # 原地编辑穿插，upgrade pin 到已被改写的 revision 内容。
        acquire_implementation_publication_lock(conn, workspace_id)
        # #1226 二轮 codex P1：锁内重读 active 重新校验判定状态（三轮 P1：
        # 重读走持锁的同一事务连接，不再二次 checkout——池打满/池容量 1 时
        # 嵌套 checkout 会饿死整个发布）：行已易主即中止（409 族，调用方对
        # 新 active 重试即收敛），on_commit hook 也随事务回滚不执行。不做
        # 自动重分类：基于新 active 重走结构路径会把本次 runtime 编辑静默
        # 升级为全量结构发布，覆盖并发方的结构变更（LWW 全文档语义），比
        # 报冲突更危险。
        current_active = job_db.read_active_workflow_revision_on(conn, workspace_id, definition.key)
        if current_active is None or current_active["id"] != active["id"]:
            raise ConflictError("Active workflow revision moved during publish; retry")
        row = update_active_revision_definition_tx(
            conn,
            revision_id=str(active["id"]),
            definition_json=definition_json,
            definition_hash=new_hash,
        )
        # 版本行 = 审计行（M47）：diff 基线取锁内重读的现内容——并发 runtime
        # 保存在本锁下串行，逐版本 diff 始终相对上一条已提交内容。
        locked_config = extract_runtime_config(
            workflow_definition_from_dict(json.loads(str(current_active["definition_json"])))
        )
        changed_keys = diff_runtime_config_keys(locked_config, new_config)
        version_row = None
        if changed_keys:
            version_row = append_runtime_config_version_tx(
                conn,
                workspace_id=workspace_id,
                revision_id=str(active["id"]),
                config_json=_serialize_config(new_config),
                changed_keys_json=_serialize_config(changed_keys),
                created_by=created_by,
            )
        # #1221: the caller's hook (draft-row delete on draft publish) rides
        # this transaction too — a hook failure rolls the in-place edit back.
        if on_commit is not None:
            on_commit(conn)
    return row, version_row


def save_workflow_runtime_config(
    job_db: JobQueries,
    workspace_id: str,
    definition: WorkflowDefinition,
    *,
    created_by: str,
) -> dict[str, Any]:
    """Runtime channel save: refuse structural changes, apply the rest in place.

    Requires an already-published workflow (the first publish belongs to the
    draft channel). A definition whose structural payload differs from the
    active revision conflicts (409) — structural edits must publish a new
    revision through the draft channel.
    """
    active = job_db.get_active_workflow_revision(workspace_id, definition.key)
    if active is None:
        raise NotFoundError(f"No active workflow revision for {definition.key}")
    current = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    if revision_structurally_changed(current, definition):
        raise ConflictError(
            "Structural workflow changes require the draft publish channel;"
            " the runtime channel only carries runtime settings"
        )
    revision, version_row = update_active_revision_runtime_in_place(
        job_db, workspace_id, definition, active=active, created_by=created_by
    )
    return {"revision": revision, "runtime_version": version_row}


def rollback_workflow_runtime_config(
    job_db: JobQueries,
    workspace_id: str,
    version: int,
    *,
    created_by: str,
) -> dict[str, Any]:
    """Re-apply a historical runtime config as a NEW appended version.

    The rollback target's slice is applied onto the current active revision
    (nodes the target does not know keep their current values — so after an
    intervening structural change the new row's config_json is the target
    slice applied to the current structure, not the target verbatim);
    history rows are never rewritten.
    """
    target = job_db.get_runtime_config_version(workspace_id, version)
    if target is None:
        raise NotFoundError(f"No runtime config version {version}")
    active = job_db.get_active_workflow_revision(workspace_id, workspace_id)
    if active is None:
        raise NotFoundError(f"No active workflow revision for {workspace_id}")
    current = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    rolled_back = apply_runtime_config(current, json.loads(str(target["config_json"])))
    revision, version_row = update_active_revision_runtime_in_place(
        job_db, workspace_id, rolled_back, active=active, created_by=created_by
    )
    return {"revision": revision, "runtime_version": version_row}
