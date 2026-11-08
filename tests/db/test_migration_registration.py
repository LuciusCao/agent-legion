from __future__ import annotations

import pytest

import server.app.db.migrations as migrations


@pytest.mark.no_db
def test_migration_package_exports_every_registered_function() -> None:
    expected = (
        "migrate_agent_catalog_cutover",
        "migrate_agent_request_kind_window",
        "migrate_agent_workspace_scope",
        "migrate_code_executor_bindings",
        "migrate_custom_node_codes",
        "migrate_execution_generation",
        "migrate_executor_asr_config_schema",
        "migrate_executor_entity_type",
        "migrate_executor_retirement",
        "migrate_external_connections",
        "migrate_hmac_connection_type",
        "migrate_job_artifacts",
        "migrate_job_status_counts_advisory_locks",
        "migrate_job_status_counts_statement_triggers",
        "migrate_jobs_run_id_index",
        "migrate_local_executor_removal",
        "migrate_node_cms_config",
        "migrate_ops_runtime_profile_samples",
        "migrate_retire_global_register_tokens",
        "migrate_run_job_status_counts",
        "migrate_runs",
        "migrate_scoped_token_origin",
        "migrate_studio_chat_context",
        "migrate_studio_chat_draft",
        "migrate_studio_chat_tables",
        "migrate_studio_publish_requests",
        "migrate_versioned_entities",
        "migrate_workflow_catalog_retirement",
        "migrate_workflow_node_explicit_types",
        "migrate_workspace_api_tokens",
        "migrate_workspace_cms_config",
        "migrate_workspace_execution_defaults",
        "migrate_workspace_id_key_binding",
        "migrate_workspace_job_node_status_counts",
        "migrate_workspace_secrets",
    )
    assert tuple(migrations.__all__) == expected
    assert all(callable(getattr(migrations, name)) for name in expected)
