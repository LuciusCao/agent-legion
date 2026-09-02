"""Chronological registry of versioned schema migrations.

Split from ``schema.py`` for the file-size budget (and from
``migration_registry.py``, which re-exports this chain, when the import
list itself outgrew the ceiling). DDL-only versions have no Python
function — their DDL lives in ``postgres_schema.sql`` — but they still get
a registry entry so ``max(version)`` stays meaningful for upgrade gating.
migrate_runs (v53) must run after every migration that still reads
job_batches; the version-sorted registry guarantees it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from server.app.db.migrations import (
    migrate_agent_catalog_cutover,
    migrate_agent_request_kind_window,
    migrate_agent_workspace_scope,
    migrate_code_executor_bindings,
    migrate_executor_asr_config_schema,
    migrate_executor_entity_type,
    migrate_executor_retirement,
    migrate_external_connections,
    migrate_hmac_connection_type,
    migrate_job_artifacts,
    migrate_jobs_run_id_index,
    migrate_local_executor_removal,
    migrate_node_cms_config,
    migrate_ops_runtime_profile_samples,
    migrate_retire_global_register_tokens,
    migrate_run_job_status_counts,
    migrate_runs,
    migrate_scoped_token_origin,
    migrate_studio_chat_context,
    migrate_studio_chat_draft,
    migrate_studio_chat_tables,
    migrate_versioned_entities,
    migrate_workflow_catalog_retirement,
    migrate_workflow_node_explicit_types,
    migrate_workspace_cms_config,
    migrate_workspace_execution_defaults,
    migrate_workspace_id_key_binding,
    migrate_workspace_job_node_status_counts,
    migrate_workspace_secrets,
)
from server.app.db.migrations.job_status_counts import migrate_workspace_job_status_counts
from server.app.db.migrations.jobs_workflow_key_alignment import (
    migrate_jobs_workflow_key_alignment,
)
from server.app.db.migrations.preview_panels import migrate_preview_panels
from server.app.db.migrations.retire_workflow_key_columns import (
    migrate_retire_workflow_key_columns,
)

MigrationFn = Callable[[Any], None]


@dataclass(frozen=True)
class SchemaMigration:
    """One versioned entry: the newest migration introduced at this version."""

    version: int
    name: str
    apply: MigrationFn | None = None


MIGRATIONS: list[SchemaMigration] = [
    SchemaMigration(13, "auth_users_sessions_workspace_members"),
    SchemaMigration(14, "workspace_node_config", migrate_node_cms_config),
    SchemaMigration(15, "workspace_cms_config_retirement", migrate_workspace_cms_config),
    SchemaMigration(16, "workspace_secrets", migrate_workspace_secrets),
    SchemaMigration(17, "agent_request_reporting_state"),
    SchemaMigration(21, "global_settings"),
    SchemaMigration(23, "ops_metric_samples_workspace_scope"),
    SchemaMigration(24, "code_executor_bindings", migrate_code_executor_bindings),
    SchemaMigration(25, "local_executor_removal", migrate_local_executor_removal),
    SchemaMigration(26, "versioned_entities", migrate_versioned_entities),
    SchemaMigration(27, "agent_catalog_cutover", migrate_agent_catalog_cutover),
    SchemaMigration(30, "executor_entity_type", migrate_executor_entity_type),
    SchemaMigration(31, "executor_asr_config_schema", migrate_executor_asr_config_schema),
    SchemaMigration(34, "external_connections", migrate_external_connections),
    SchemaMigration(36, "workspace_job_status_counts", migrate_workspace_job_status_counts),
    SchemaMigration(42, "scoped_token_origin", migrate_scoped_token_origin),
    SchemaMigration(43, "studio_chat_tables", migrate_studio_chat_tables),
    SchemaMigration(44, "hmac_connection_type", migrate_hmac_connection_type),
    SchemaMigration(45, "studio_chat_context", migrate_studio_chat_context),
    SchemaMigration(46, "agent_workspace_scope", migrate_agent_workspace_scope),
    SchemaMigration(47, "executor_retirement", migrate_executor_retirement),
    SchemaMigration(50, "workflow_catalog_retirement", migrate_workflow_catalog_retirement),
    SchemaMigration(51, "agent_request_kind_window", migrate_agent_request_kind_window),
    SchemaMigration(53, "runs", migrate_runs),
    SchemaMigration(54, "job_artifacts", migrate_job_artifacts),
    SchemaMigration(55, "material_bundles"),
    SchemaMigration(56, "job_node_status_counts", migrate_workspace_job_node_status_counts),
    SchemaMigration(57, "studio_chat_draft", migrate_studio_chat_draft),
    # v58: retire all-workspaces register tokens (#35) runs last so legacy
    # NULL-workspace rows are revoked before any scoped-token traffic can
    # observe them.
    SchemaMigration(58, "retire_global_register_tokens", migrate_retire_global_register_tokens),
    # v59: jobs(run_id) index for the batch-queue run-scoped lookups. DDL-only
    # but carries an apply fn (not just a schema-file entry): the schema file
    # replays before v53's batch_id→run_id rename on v52 upgrades.
    SchemaMigration(59, "jobs_run_id_index", migrate_jobs_run_id_index),
    # v60 is DDL-only (agent_workers.register_token_ids_json): the idempotent
    # schema-file replay adds the column, no data migration needed.
    SchemaMigration(60, "worker_register_token_ids"),
    # v61 is DDL-only (workspace_workflow_drafts): the Studio workflow YAML
    # draft table comes from the schema-file replay, no data migration.
    SchemaMigration(61, "workspace_workflow_drafts"),
    # v62: bind workspace id and workflow key (rename ids to their bound
    # keys, backfill empty keys from the id). Runs last: it rewrites
    # workspace_id rows in every child table and the agent_workers scope
    # JSON, so it must see every other workspace-shape change settled.
    SchemaMigration(62, "workspace_id_key_binding", migrate_workspace_id_key_binding),
    # v63 is DDL-only (workspaces.preview_config_json): workspace-level
    # artifact preview config comes from the schema-file replay, no data
    # migration.
    SchemaMigration(63, "workspace_preview_config"),
    # v64 retires the workspace-level Agent defaults: the data migration
    # backfills non-empty default_agent_* values into the active revision's
    # top-level execution block (migrations/workspace_execution_defaults.py),
    # then the three columns (plus the fully retired intake_config_json) are
    # dropped in the post-chain cleanup sweep
    # (migrations/workspace_settings_retirement.py, called from schema.py)
    # because the v62 data migration still replays inserts that
    # reference them on older databases.
    SchemaMigration(64, "workspace_settings_retirement", migrate_workspace_execution_defaults),
    # v65 is DDL-only (approval_decisions, EXEC-APPROVAL-001): the
    # human-approval-gate audit table comes from the schema-file replay.
    SchemaMigration(65, "approval_decisions"),
    # v66: explicit workflow node types (#284 phase 2). Backfills
    # node_type ("agent"|"code") into active revisions from the
    # workspace_node_routes projection and into Studio draft YAML, so the
    # loader normalization (legacy "node"/missing → "code") produces no
    # ghost structural diff on the next save. Approval gates already carry
    # an explicit type and are skipped.
    SchemaMigration(66, "workflow_node_explicit_types", migrate_workflow_node_explicit_types),
    # v67 is DDL-only (#211 Phase 3 read-layer binding): workspace-keyed
    # twins of idx_jobs_active_marks / idx_jobs_workflow_updated come from
    # the schema-file replay — the job-scan predicates now bind workspace_id
    # (workflow_key equals it since v62).
    SchemaMigration(67, "jobs_workspace_scan_indexes"),
    # v68: align jobs.workflow_key with the v62 binding (#211 Phase 3 read
    # binding) — v62's rename left old keys on upgraded rows; the scan
    # predicates now key on workspace_id, so stored values must match.
    SchemaMigration(68, "jobs_workflow_key_alignment", migrate_jobs_workflow_key_alignment),
    # v69 is DDL-only (#211 Phase 3 M1): the workspace-keyed lease-count
    # index (claim path predicate binds workspace_id — Codex P2 on #321)
    # comes from the schema-file replay.
    SchemaMigration(69, "executor_leases_workspace_index"),
    # v70 (#211 Phase 3 M2): retire the workflow_key columns — composite-PK
    # state tables rebuild without the key, jobs/runs/leases/requests/
    # revisions drop the column, six key-leading indexes swap for
    # workspace-keyed twins. Requires v68 alignment (values all equal the
    # workspace id) and M1's predicate/write normalization.
    SchemaMigration(70, "retire_workflow_key_columns", migrate_retire_workflow_key_columns),
    # v71 (#328): widen the versioned_entities entity_type CHECK so
    # workspace-scoped preview panel bundles join the draft → published
    # lifecycle; carried as an apply fn because create-table-if-not-exists
    # never rewrites the CHECK on existing databases (drop + re-add).
    SchemaMigration(71, "preview_panels", migrate_preview_panels),
    # v72 (#359 L1): runtime-profile gauge table; seeded to the ops series.
    SchemaMigration(72, "ops_runtime_profile_samples", migrate_ops_runtime_profile_samples),
    # v73 (#358): run-level job status counters. count_jobs_by_status_in_run
    # was a group-by over the run's whole jobs slice; the trigger-maintained
    # run_job_status_counts (DB-RUN-JOB-STATUS-COUNTS-001) turns the run
    # detail read into a PK lookup and is the data source for the #350 run
    # progress view.
    SchemaMigration(73, "run_job_status_counts", migrate_run_job_status_counts),
    # v74 is DDL-only (#368): the studio_chat_sessions session_modes_json /
    # config_options_json mirrors come from the schema-file replay (its
    # alter-if-not-exists covers pre-v74 tables), no data migration.
    SchemaMigration(74, "studio_chat_agent_config"),
]

_VERSIONS = [m.version for m in MIGRATIONS]
assert sorted(_VERSIONS) == _VERSIONS, "MIGRATIONS must stay version-sorted"
