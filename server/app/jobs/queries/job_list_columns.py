"""``JobQueries.list_jobs`` projection (#957).

Every ``jobs`` column except the KB-scale intake payloads (``input_json`` /
``frozen_config_json``), which no list consumer reads (job summaries, preview
context, studio tools, stress seeding). The definition snapshot stays:
summaries parse it for node order and labels. A new ``jobs`` column must be
added here deliberately — tests/db/test_job_list_columns.py pins the
projection to "all columns minus the intake payloads".
"""

from __future__ import annotations

JOB_LIST_COLUMNS = (
    "id, workspace_id, source_type, source_id, run_id, title, status, storage_dir,"
    " error_message, stem, created_at, updated_at, execution_mode, target_node_key,"
    " execution_paused, pause_reason, packed, workflow_revision_id,"
    " workflow_definition_hash, workflow_definition_snapshot_json, outcome,"
    " workflow_version, execution_generation"
)
