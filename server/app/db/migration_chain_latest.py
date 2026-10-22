"""Newest versioned schema migrations (v102+), spliced after
``migration_chain_recent.RECENT_MIGRATIONS`` (second file-budget split, M47:
the recent file's allowance band is full). Append new versions here;
migration_chain asserts the combined registry stays sorted."""

from __future__ import annotations

from server.app.db.migration_entry import SchemaMigration
from server.app.db.migrations.job_batch_operations import (
    migrate_job_batch_operations_archive,
)
from server.app.db.migrations.workflow_runtime_config_versions import (
    migrate_workflow_runtime_config_versions,
)

LATEST_MIGRATIONS: list[SchemaMigration] = [
    # v102 (M47): workflow_runtime_config_versions — runtime 通道（agent 节点
    # provider/model/thinking/prompt/prompt_mode/tools/requires_labels + 顶层
    # execution 同类默认键）每次保存的追加式不可变版本表，审计与回滚来源；
    # dispatch 仍解析 active revision 的 definition_json，本表不参与解析链。
    # Born as this branch's v99, renumbered twice (#434 collision protocol:
    # the later merge renumbers): v99→v100 when main took v99 for run_batches,
    # v100→v102 when main took v100 (run_batches_archive) and v101
    # (job_batch_operations).
    # DDL-only, table created by its migration module (schema file at its
    # raw-line ceiling; v96 precedent).
    SchemaMigration(
        102, "workflow_runtime_config_versions", migrate_workflow_runtime_config_versions
    ),
    # v103 (M61): job_batch_operations.archived_at — 任务中心批量操作卡片的
    # 归档位（手动归档置位；自动归档为查询口径，不落列；90 天归档 retention
    # 走 run_batches 同一 sweeper 路径）。与 v100 同形同语义，迁移函数与
    # v101 共用模块（v99/v100 先例）。DDL-only, same guarded-ALTER home rule
    # as v87.
    SchemaMigration(103, "job_batch_operations_archive", migrate_job_batch_operations_archive),
]
