"""The stateless publish pipeline for one workflow revision (#287).

Why a separate module: publishing is a fixed sequence — serialize the
definition, freeze the node_code_pins snapshot (EXEC-CODE-002), embed it
beside the definition, allocate the next version, derive Agent routes, and
hand the atomic revision + projection write to the JobQueries facade. None
of it touches service state, so it lives as a free function next to the
shared route derivation (workflow_revision_routes.py);
``WorkflowRevisionService`` stays the constructor-holding facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.agent_node_profile_catalog import legacy_agent_catalog
from server.app.services.agent_profile_provenance import (
    carry_forward_provenance,
    embed_provenance,
)
from server.app.services.node_code_resolution import freeze_node_code_versions
from server.app.services.node_config_prune import override_prune_commit_hook
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.services.workflow_revision_routes import (
    derive_agent_routes,
    has_legacy_agent_nodes,
)
from server.app.services.workflow_revision_runtime import embed_node_code_pins
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def publish_workflow_revision(
    job_db: JobQueries,
    custom_nodes_enabled: bool,
    workspace_id: str,
    definition: WorkflowDefinition,
) -> dict:
    definition_json = serialize_definition(definition)
    # node_code_pins snapshot the published custom code versions at publish
    # time (EXEC-CODE-002, design §4): publish-moment state embedded via
    # embed_node_code_pins — inside definition_json, outside
    # definition_hash. Since #115 they are an audit record and the
    # quality-replay pin source; ordinary jobs dispatch the latest
    # published code instead.
    pins = freeze_node_code_versions(
        job_db,
        custom_nodes_enabled,
        workspace_id,
        definition.key,
        list(definition.executable_nodes),
    )
    stored_json = embed_node_code_pins(definition_json, pins)
    # #935：v93 回填写下的 agent_profile_provenance 对档案字段未变的节点
    # 随新 revision 延续（同 node_code_pins，不计入 definition_hash）——
    # 升级 diff 归一靠它识别「旧快照 legacy 节点 == 内联后的节点」。
    previous = job_db.get_active_workflow_revision(workspace_id, definition.key)
    stored_json = embed_provenance(
        stored_json,
        carry_forward_provenance(
            str(previous["definition_json"]) if previous is not None else None, definition
        ),
    )
    version = job_db.next_workflow_revision_version(workspace_id, definition.key)
    revision_id = f"{workspace_id}:{definition.key}:v{version}"
    agent_routes: dict[str, str] | None = derive_agent_routes(job_db, workspace_id, definition)
    # #935 route 停写（#440 P3）：门禁要求 agent 节点自含后，产品发布路径
    # 只会发布全自含 revision——它不碰 workspace_node_routes（不 upsert、
    # 不 prune），存量行冻结只读，服务旧快照 legacy 节点的在途 job。仅内部
    # 种子路径（demo builtin / ensure_active_revision，P4 改 YAML 自含）仍
    # 可能带 legacy 节点并照旧物化。
    if not has_legacy_agent_nodes(definition):
        agent_routes = None
    # The new revision's schemas are the live truth for the workspace's
    # node overrides: keys/values it no longer accepts must go so intake
    # keeps working after a schema rename/removal (#428 二轮复审 P2-1).
    # The prune is planned here (pure read, no writes) and applied inside
    # the revision transaction below: a prune failure then rolls the whole
    # publish back instead of stranding an active revision whose stale
    # overrides still block every new job's intake (codex 终轮 P1-3).
    prune_hook = override_prune_commit_hook(
        job_db, workspace_id, definition, legacy_agent_catalog(job_db, workspace_id)
    )
    return job_db.create_workflow_revision(
        revision_id=revision_id,
        workspace_id=workspace_id,
        workflow_key=definition.key,
        version=version,
        status="active",
        definition_json=stored_json,
        definition_hash=definition_hash(definition_json),
        agent_routes=agent_routes,
        on_commit=prune_hook,
    )
