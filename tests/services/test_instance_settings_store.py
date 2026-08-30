from __future__ import annotations

import pytest

from server.app.services.instance_settings import (
    default_instance_document,
    effective_instance_document,
)
from server.app.services.instance_settings_store import InstanceSettingsStore


@pytest.fixture
def store(job_db) -> InstanceSettingsStore:
    store = InstanceSettingsStore(job_db.dsn_identity)
    with job_db.connect() as conn:
        conn.execute("delete from global_settings where key='instance'")
    return store


def _document() -> dict:
    return default_instance_document()


def test_get_returns_none_when_unset(store) -> None:
    assert store.get() is None


def test_put_get_roundtrip(store) -> None:
    document = _document()
    document["lease_ttl_seconds"] = 120
    store.put(document)
    assert store.get() == document


def test_put_overwrites_existing_document(store) -> None:
    store.put(_document())
    updated = _document()
    updated["cleanup"]["log_retention_days"] = 14
    store.put(updated)
    assert store.get()["cleanup"]["log_retention_days"] == 14


def test_default_document_matches_retired_yaml_values() -> None:
    document = default_instance_document()
    assert document["cleanup"] == {
        "log_retention_days": 7,
        "run_dir_retention_days": 3,
        "interval_seconds": 3600,
    }
    assert document["monitoring"] == {"sample_interval_seconds": 60, "retention_days": 30}
    assert document["heartbeat_interval_seconds"] == 10
    assert document["lease_ttl_seconds"] == 90
    assert document["heartbeat_failure_threshold"] == 3
    assert document["sweeper_enabled"] is True
    assert document["sweeper_interval_seconds"] == 5.0
    assert document["workflows"] == {"enabled": True}
    assert document["agent_workers"] == {
        "max_archive_bytes": 64 * 1024 * 1024,
        "min_protocol_version": 1,
    }
    # openclaw defaults are cwd-only now: the retired knobs
    # (command_template/skill_safety/...) were configurable but never consumed.
    assert document["openclaw"] == {"cwd": "."}


def test_effective_document_merges_partial_stored_over_defaults() -> None:
    effective = effective_instance_document({"lease_ttl_seconds": 45})
    assert effective["lease_ttl_seconds"] == 45
    # Untouched keys keep their code defaults.
    assert effective["heartbeat_failure_threshold"] == 3
    assert effective["cleanup"]["interval_seconds"] == 3600


def test_effective_document_deep_merges_nested_sections() -> None:
    effective = effective_instance_document({"agent_workers": {"max_archive_bytes": 1024}})
    assert effective["agent_workers"]["max_archive_bytes"] == 1024
    assert effective["agent_workers"]["min_protocol_version"] == 1
