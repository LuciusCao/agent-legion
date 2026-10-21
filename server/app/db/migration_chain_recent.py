"""Most recent versioned schema migrations (v87+), spliced onto the end of
``migration_chain.MIGRATIONS`` (file-budget split, #924). Append new
versions here; migration_chain asserts the combined registry stays sorted."""

from __future__ import annotations

from server.app.db.migration_entry import SchemaMigration
from server.app.db.migrations import run_batches_archive
from server.app.db.migrations.agent_profile_backfill import migrate_agent_profile_backfill
from server.app.db.migrations.agent_request_profile_source import (
    migrate_agent_request_profile_source,
)
from server.app.db.migrations.agent_worker_claim_state import migrate_agent_worker_claim_state
from server.app.db.migrations.agent_worker_node_limits import migrate_agent_worker_node_limits
from server.app.db.migrations.job_batch_operations import migrate_job_batch_operations
from server.app.db.migrations.job_node_status_count_deltas import (
    migrate_job_node_status_count_deltas as _migrate_v88_node_deltas,
)
from server.app.db.migrations.material_object_etag import migrate_material_object_etag
from server.app.db.migrations.material_upload_generation import migrate_material_upload_generation
from server.app.db.migrations.retire_default_workflow_key import (
    migrate_retire_default_workflow_key,
)
from server.app.db.migrations.studio_chat_message_draft_kind import (
    migrate_studio_chat_message_draft_kind,
)
from server.app.db.migrations.studio_chat_session_archive import migrate_studio_chat_session_archive
from server.app.db.migrations.studio_chat_session_soft_delete import (
    migrate_studio_chat_session_soft_delete,
)
from server.app.db.migrations.workflow_draft_generation import migrate_workflow_draft_generation

RECENT_MIGRATIONS: list[SchemaMigration] = [
    # v87: Worker-reported claim switch column (agent_workers.claim_enabled,
    # nullable) — the Host UI's「在线·未领取」signal. Born as this branch's
    # v83, bumped to 87 after the base advanced to v86 (#434 collision
    # protocol: the later merge renumbers). DDL-only, guarded rule.
    SchemaMigration(87, "agent_worker_claim_state", migrate_agent_worker_claim_state),
    # v88 (#690): v82's append-and-fold protocol for the job NODE counter
    # family. The row trigger's per-node upsert on shared (workspace,
    # node_key, status) rows closed the same cross-transaction AB-BA ring
    # v82 removed from the job family; a try-lock (class 88) folder now
    # alone writes a workspace's base rows and every other writer appends a
    # delta, so no write in the family ever waits. bump_job_node_status_counts
    # moved out of postgres_schema.sql into this migration's SQL so a later
    # schema-file replay cannot restore the blocking body.
    SchemaMigration(88, "job_node_status_count_deltas", _migrate_v88_node_deltas),
    # v89 (#872): studio_chat_sessions.deleted_at — Studio chat session soft
    # delete (list filters it, public reads 404, resume claim refuses it).
    # A column, not a status value: status is the live runtime state
    # machine; deletion is an orthogonal visibility flag. DDL-only, same
    # guarded-ALTER home rule as v87.
    SchemaMigration(89, "studio_chat_session_soft_delete", migrate_studio_chat_session_soft_delete),
    # v90 (#924): studio_chat_sessions.archived_at — recoverable session
    # archive (default list hides it, resume claim + spawn fence refuse it
    # until unarchive). Column, not status value, same argument as v89.
    SchemaMigration(90, "studio_chat_session_archive", migrate_studio_chat_session_archive),
    # v91 (#211 M3): drop workspaces.default_workflow_key and
    # quality_sample_batches.workflow_key — both equal the workspace id, so
    # the id is the only identifier left. Guarded (drop if exists): fresh
    # databases never create them.
    SchemaMigration(91, "retire_default_workflow_key", migrate_retire_default_workflow_key),
    # v92 (#933, #440 P2): agent_execution_requests.profile_source / runtime /
    # requires_labels_json — self-contained agent node rows carry their
    # frozen profile and skip the versioned_entities join at claim. Follows
    # v91 (#211 M3) in merge order (#434 protocol).
    # DDL-only, same guarded-ALTER home rule as v87.
    SchemaMigration(92, "agent_request_profile_source", migrate_agent_request_profile_source),
    # v93 (#935, #440 P3): inline each legacy agent node's published Agent
    # definition into the node (active revisions + Studio drafts), backup
    # table first, provenance sibling outside definition_hash. Data
    # migration, idempotent (self-contained nodes skipped), no down.
    SchemaMigration(93, "agent_profile_backfill", migrate_agent_profile_backfill),
    # v94 (#1158): agent_workers.node_concurrency_limits_json — Worker 节点级
    # 并发上限的库存面（claim 时热同步，机器资源保护层）。DDL-only, same
    # guarded-ALTER home rule as v87.
    SchemaMigration(94, "agent_worker_node_limits", migrate_agent_worker_node_limits),
    # v95 (#1123): studio_chat_messages kind check gains 'draft' — the
    # synthetic draft_saved timeline message emitted by the studio-agent save
    # tools so subagent saves also produce draft cards. DDL-only, same
    # guarded-ALTER home rule as v87.
    SchemaMigration(95, "studio_chat_message_draft_kind", migrate_studio_chat_message_draft_kind),
    # v96 (#1226 五轮收口, 设计 v2): workspace_workflow_draft_generation —
    # the workspace draft generation counter the CAS save bases on (stale
    # base → 409, publish delete bumps). Renumbered v95→v96 after main took
    # v95 for studio_chat_message_draft_kind (#434 collision protocol).
    # DDL-only, table created by its migration module (schema file at its
    # raw-line ceiling; v76 precedent).
    SchemaMigration(96, "workflow_draft_generation", migrate_workflow_draft_generation),
    # v97 (#1267 codex R3 P1): materials.upload_generation — 每次 presign 重试
    # +1 的上传代次，让同 key/同大小的 uploading→uploading 重试对 complete
    # 的锁内复查可见。Born as this branch's v95, renumbered twice (#434
    # collision protocol: the later merge renumbers): v95→v96 when main took
    # v95 for studio_chat_message_draft_kind, then v96→v97 when #1226 took
    # v96 for workflow_draft_generation. DDL-only, same guarded-ALTER home
    # rule as v87.
    SchemaMigration(97, "material_upload_generation", migrate_material_upload_generation),
    # v98 (#1279): materials.object_etag — 落 ready 时记录的对象 ETag（complete
    # 锁内原子 / inline / demo seed），消费侧据以检出 complete 后同尺寸覆盖；
    # 存量行 default '' 维持 size-only（身份建立拆至 #1316）。DDL-only,
    # same guarded-ALTER home rule as v87.
    SchemaMigration(98, "material_object_etag", migrate_material_object_etag),
    # v99 (#1117): run_batches — runs 异步模式的 batch 状态行（target_spec +
    # 游标 + 计数器 + 状态机；基础设施，非 Campaign 产品实体）。Table DDL
    # rides the apply fn (schema file at its raw-line ceiling; v76/v96
    # precedent).
    # v99/v100 共用一个模块级导入（本文件贴着 growth-allowance 带上沿，
    # 第二条 import 行会超带）：v99 fn 经 v100 模块 re-export。
    SchemaMigration(99, "run_batches", run_batches_archive.migrate_run_batches),
    # v100 (#1157 phase3): run_batches.archived_at — 任务中心归档位（手动归档
    # 置位；自动归档为查询口径，不落列）。DDL-only, same guarded-ALTER home
    # rule as v87.
    SchemaMigration(100, "run_batches_archive", run_batches_archive.migrate_run_batches_archive),
    # v101 (#946): job_batch_operations + job_batch_operation_results —
    # 批量 job 操作异步化的任务行与逐 job 结果子表（runs-async 基建复用到
    # 变更类批量端点）。Born as this branch's v100, renumbered after main
    # took v100 for run_batches_archive (#434 collision protocol). Table DDL
    # rides the apply fn (same precedent).
    SchemaMigration(101, "job_batch_operations", migrate_job_batch_operations),
]
