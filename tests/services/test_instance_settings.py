"""Hydration tests for apply_instance_settings (startup DB overlay)."""

from __future__ import annotations

import pytest

from server.app.services.instance_settings import apply_instance_settings
from server.app.services.instance_settings_store import InstanceSettingsStore


@pytest.fixture
def store(job_db) -> InstanceSettingsStore:
    store = InstanceSettingsStore(job_db.path)
    with job_db.connect() as conn:
        conn.execute("delete from global_settings where key='instance'")
    return store


def test_apply_is_noop_without_stored_document(settings, job_db, store) -> None:
    before_runtime = settings.executor_runtime.model_dump()
    before_cleanup = dict(settings.config["cleanup"])

    apply_instance_settings(settings, job_db.path)

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

    apply_instance_settings(settings, job_db.path)

    runtime = settings.executor_runtime
    assert runtime.lease_ttl_seconds == 120
    assert runtime.heartbeat_interval_seconds == 2.5
    assert runtime.sweeper_enabled is False
    assert runtime.workflows.enabled is False
    assert runtime.agent_workers.max_archive_bytes == 1024
    # Keys absent from the stored document keep the loaded/default values.
    assert runtime.heartbeat_failure_threshold == 3
    assert runtime.agent_workers.min_protocol_version == 1
    # Sub-blocks the DB does not manage keep their loaded values.
    assert runtime.workflows.pi.binary == "pi"
    # openclaw is DB-managed now: absent from the stored document it falls
    # back to the code defaults (= the retired yaml values).
    assert runtime.openclaw.command_template[0] == "openclaw"
    assert runtime.openclaw.skill_safety.enabled is True
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
        apply_instance_settings(settings, job_db.path)


def test_apply_overrides_openclaw_block(settings, job_db, store) -> None:
    store.put(
        {
            "openclaw": {
                "cwd": "/tmp/openclaw-db",
                "timeout_seconds": 300,
                "command_template": ["openclaw", "agent", "--json"],
                "skill_safety": {"enabled": False, "repos": [{"path": "~/skills/s1"}]},
            }
        }
    )

    apply_instance_settings(settings, job_db.path)

    openclaw = settings.executor_runtime.openclaw
    assert openclaw.cwd == "/tmp/openclaw-db"
    assert openclaw.timeout_seconds == 300
    assert openclaw.command_template == ("openclaw", "agent", "--json")
    assert openclaw.skill_safety.enabled is False
    assert [repo.path for repo in openclaw.skill_safety.repos] == ["~/skills/s1"]
    # Keys absent from the stored block fall back to the code defaults.
    assert openclaw.isolated_workspace_root == ""


def test_apply_openclaw_env_cwd_outranks_db_document(settings, job_db, store, monkeypatch) -> None:
    """AGENT_LEGION_OPENCLAW_CWD keeps top priority over the DB document."""
    monkeypatch.setenv("AGENT_LEGION_OPENCLAW_CWD", "/tmp/openclaw-env")
    store.put({"openclaw": {"cwd": "/tmp/openclaw-db"}})

    apply_instance_settings(settings, job_db.path)

    assert settings.executor_runtime.openclaw.cwd == "/tmp/openclaw-env"
