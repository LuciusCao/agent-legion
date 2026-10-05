"""Most recent versioned schema migrations (v87+), spliced onto the end of
``migration_chain.MIGRATIONS`` (file-budget split, #924). Append new
versions here; migration_chain asserts the combined registry stays sorted."""

from __future__ import annotations

from server.app.db.migration_entry import SchemaMigration
from server.app.db.migrations.agent_worker_claim_state import migrate_agent_worker_claim_state
from server.app.db.migrations.job_node_status_count_deltas import (
    migrate_job_node_status_count_deltas as _migrate_v88_node_deltas,
)
from server.app.db.migrations.studio_chat_session_archive import (
    migrate_studio_chat_session_archive,
)
from server.app.db.migrations.studio_chat_session_soft_delete import (
    migrate_studio_chat_session_soft_delete,
)

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
]
