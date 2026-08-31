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
            "workflows": {"enabled": False},
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
    assert runtime.workflows.enabled is False
    assert runtime.agent_workers.max_archive_bytes == 1024
    # Keys absent from the stored document keep the loaded/default values.
    assert runtime.heartbeat_failure_threshold == 3
    assert runtime.agent_workers.min_protocol_version == 1
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


def test_apply_strips_retired_openclaw_block(settings, job_db, store) -> None:
    """The openclaw block retired with the openclaw runtime (#75): stored
    documents carrying it hydrate cleanly and the block has no effect."""
    store.put({"openclaw": {"cwd": "/tmp/openclaw-db"}, "lease_ttl_seconds": 120})

    apply_instance_settings(settings, job_db.dsn_identity)

    assert settings.executor_runtime.lease_ttl_seconds == 120
    assert not hasattr(settings.executor_runtime, "openclaw")


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
            "skill_safety": {"enabled": True, "repos": [{"path": "~/.skills/s1"}]},
        }
    }

    document = effective_instance_document(stored)

    assert "openclaw" not in document
    # The full response contract validates against the normalized document.
    InstanceSettingsResponse.model_validate(document)
    # The caller's stored document is not mutated.
    assert "command_template" in stored["openclaw"]
