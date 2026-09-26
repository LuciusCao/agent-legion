"""Hydration tests for apply_instance_settings (startup DB overlay)."""

from __future__ import annotations

import pytest

from server.app.services.instance_settings import apply_instance_settings
from server.app.services.instance_settings_store import InstanceSettingsStore


@pytest.fixture
def store(job_db) -> InstanceSettingsStore:
    store = InstanceSettingsStore(job_db.dsn_identity)
    with job_db.connect() as conn:
        conn.execute("delete from global_settings where key='instance'")
    return store


def test_apply_is_noop_without_stored_document(settings, job_db, store) -> None:
    before_runtime = settings.executor_runtime.model_dump()
    before_cleanup = dict(settings.config["cleanup"])

    apply_instance_settings(settings, job_db.dsn_identity)

    assert settings.executor_runtime.model_dump() == before_runtime
    assert settings.config["cleanup"] == before_cleanup


def test_apply_overrides_executor_runtime_and_writes_back_config(settings, job_db, store) -> None:
    store.put(
        {
            "lease_ttl_seconds": 120,
            "heartbeat_interval_seconds": 2.5,
            "sweeper_enabled": False,
            "workflows": {"enabled": False, "max_items_per_run": 500},
            "agent_workers": {"max_archive_bytes": 1024},
            "cleanup": {"log_retention_days": 30, "interval_seconds": 60},
            "monitoring": {"sample_interval_seconds": 15},
        }
    )

    apply_instance_settings(settings, job_db.dsn_identity)

    runtime = settings.executor_runtime
    assert runtime.lease_ttl_seconds == 120
    assert runtime.heartbeat_interval_seconds == 2.5
    assert runtime.sweeper_enabled is False
    # The stored workflows.enabled key is retired (#385/#389): stripped at
    # read time; max_items_per_run still hydrates.
    assert runtime.workflows.max_items_per_run == 500
    assert not hasattr(runtime.workflows, "enabled")
    assert runtime.agent_workers.max_archive_bytes == 1024
    # Keys absent from the stored document keep the loaded/default values.
    assert runtime.heartbeat_failure_threshold == 3
    assert runtime.agent_workers.min_protocol_version == 1
    # #521 gate knob: hydrates like its agent_workers siblings.
    assert runtime.agent_workers.max_concurrent_result_commits == 16
    # #509/#554/#561 capacity knobs: absent from the stored document → code
    # defaults (legacy documents need no migration).
    assert runtime.agent_enqueue.workers == 48
    assert runtime.agent_enqueue.max_pending == 1024
    assert runtime.result_unpack.workers == 0
    assert runtime.result_validate.workers == 0
    assert runtime.agent_claim.worker_touch_interval_seconds == 30
    # #591 group-commit kill-switch: same legacy-document default.
    assert runtime.agent_workers.result_commit_batching is True
    # cleanup/monitoring are written back into the config dict, merged over
    # defaults (run_dir_retention_days was not in the stored document).
    assert settings.config["cleanup"] == {
        "log_retention_days": 30,
        "run_dir_retention_days": 3,
        "interval_seconds": 60,
    }
    assert settings.config["monitoring"] == {"sample_interval_seconds": 15, "retention_days": 30}


def test_apply_revalidates_executor_runtime_constraints(settings, job_db, store) -> None:
    store.put({"lease_ttl_seconds": 0})

    with pytest.raises(ValueError):
        apply_instance_settings(settings, job_db.dsn_identity)


def test_apply_hydrates_result_commit_gate(settings, job_db, store) -> None:
    """#521: max_concurrent_result_commits rides the agent_workers hydration
    (stored over code default; 0 is the valid kill-switch value)."""
    store.put({"agent_workers": {"max_concurrent_result_commits": 4}})

    apply_instance_settings(settings, job_db.dsn_identity)

    assert settings.executor_runtime.agent_workers.max_concurrent_result_commits == 4

    store.put({"agent_workers": {"max_concurrent_result_commits": 0}})
    apply_instance_settings(settings, job_db.dsn_identity)
    assert settings.executor_runtime.agent_workers.max_concurrent_result_commits == 0


def test_apply_hydrates_capacity_knobs(settings, job_db, store) -> None:
    """#509/#554/#569/#561/#591: agent_enqueue / result_unpack /
    result_validate / agent_claim / agent_workers.result_commit_batching
    ride the nested-block hydration like the workflows precedents."""
    store.put(
        {
            "agent_enqueue": {"workers": 64, "max_pending": 2048},
            "result_unpack": {"workers": 8},
            "result_validate": {"workers": 6},
            "agent_claim": {"worker_touch_interval_seconds": 7.5},
            "agent_workers": {"result_commit_batching": False},
        }
    )

    apply_instance_settings(settings, job_db.dsn_identity)

    assert settings.executor_runtime.agent_enqueue.workers == 64
    assert settings.executor_runtime.agent_enqueue.max_pending == 2048
    assert settings.executor_runtime.result_unpack.workers == 8
    assert settings.executor_runtime.result_validate.workers == 6
    assert settings.executor_runtime.agent_claim.worker_touch_interval_seconds == 7.5
    assert settings.executor_runtime.agent_workers.result_commit_batching is False


def test_apply_strips_retired_openclaw_block(settings, job_db, store) -> None:
    """The openclaw block retired with the openclaw runtime (#75): stored
    documents carrying it hydrate cleanly and the block has no effect."""
    store.put({"openclaw": {"cwd": "/tmp/openclaw-db"}, "lease_ttl_seconds": 120})

    apply_instance_settings(settings, job_db.dsn_identity)

    assert settings.executor_runtime.lease_ttl_seconds == 120
    assert not hasattr(settings.executor_runtime, "openclaw")


def test_apply_hydrates_node_code_max_bytes_with_env_fallback(settings, job_db, store) -> None:
    """#786: node_code_max_bytes became instance-settings managed (reversing
    the #628 env-only decision). Resolution chain: stored document > env
    (AGENT_LEGION_NODE_CODE_MAX_BYTES) > 64KB code default — a legacy stored
    document without the key must not clobber the operator's env value."""
    settings.executor_runtime.workflows.node_code_max_bytes = 128 * 1024
    store.put({"workflows": {"max_items_per_run": 500}})

    apply_instance_settings(settings, job_db.dsn_identity)

    runtime = settings.executor_runtime
    assert runtime.workflows.max_items_per_run == 500  # managed key hydrates
    # Legacy document without the key: the env-loaded value survives.
    assert runtime.workflows.node_code_max_bytes == 128 * 1024

    # A stored value (admin PUT) wins over env.
    store.put({"workflows": {"node_code_max_bytes": 256 * 1024}})
    apply_instance_settings(settings, job_db.dsn_identity)
    assert settings.executor_runtime.workflows.node_code_max_bytes == 256 * 1024


def test_effective_document_node_code_max_bytes_falls_back_to_loaded_runtime() -> None:
    """#786: the GET-side effective document takes node_code_max_bytes from
    the loaded runtime (env > code default) when the stored document lacks
    the key, so the admin form shows the value that would actually apply."""
    from server.app.configuration.executor_runtime import ExecutorRuntimeConfig
    from server.app.services.instance_settings import effective_instance_document

    runtime = ExecutorRuntimeConfig()
    runtime.workflows.node_code_max_bytes = 128 * 1024

    document = effective_instance_document({"workflows": {"max_items_per_run": 500}}, runtime)

    assert document["workflows"]["node_code_max_bytes"] == 128 * 1024
    # A stored value still wins over the loaded runtime.
    overridden = effective_instance_document(
        {"workflows": {"node_code_max_bytes": 256 * 1024}}, runtime
    )
    assert overridden["workflows"]["node_code_max_bytes"] == 256 * 1024


def test_effective_document_strips_retired_openclaw_block_from_stored_document() -> None:
    """Deployments upgraded from before the openclaw retirement (#75) still
    carry an openclaw block in global_settings['instance']; the effective
    document must drop it wholesale so the extra=forbid response model
    validates (otherwise GET /api/admin/instance-settings would 500)."""
    from server.app.routes.instance_settings_contracts import InstanceSettingsResponse
    from server.app.services.instance_settings import effective_instance_document

    stored = {
        "openclaw": {
            "cwd": "/tmp/openclaw-db",
            "command_template": ["openclaw", "agent"],
            "skill_safety": {"repos": [{"path": "~/.skills/s1"}]},
        }
    }

    document = effective_instance_document(stored)

    assert "openclaw" not in document
    # The full response contract validates against the normalized document.
    InstanceSettingsResponse.model_validate(document)
    # The caller's stored document is not mutated.
    assert "command_template" in stored["openclaw"]


def test_effective_document_strips_retention_cursor_block() -> None:
    """The retention sweep's persisted keyset cursors (#354) ride the stored
    instance document but are host-private state: the effective document must
    drop them so the extra=forbid response model validates."""
    from server.app.routes.instance_settings_contracts import InstanceSettingsResponse
    from server.app.services.instance_settings import effective_instance_document

    stored = {
        "execution_retention_days": 30,
        "execution_retention_cursor": {
            "requests:done": {"at": "2026-01-01T00:00:00+00:00", "id": "exec-1"}
        },
    }

    document = effective_instance_document(stored)

    assert "execution_retention_cursor" not in document
    assert document["execution_retention_days"] == 30
    InstanceSettingsResponse.model_validate(document)
