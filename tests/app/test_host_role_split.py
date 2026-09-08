"""Host role split (#521 方案 B): role parsing and factory dispatch.

Unit tests for the env parsing and the create_app guards; the plane
composition (threads on/off, reset gating, sampling ownership) is pinned
by tests/app/test_main.py, and the live PostgreSQL round-trip (NOTIFY
bridge + per-plane probe locks) by tests/db/test_role_split_postgres.py.
"""

from __future__ import annotations

import pytest

from server.app.configuration.host_role import (
    ROLE_COMBINED,
    ROLE_ENV,
    ROLE_HTTP,
    ROLE_SCHEDULER,
    host_role_from_env,
)

pytestmark = pytest.mark.no_db


def test_default_role_is_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ROLE_ENV, raising=False)
    assert host_role_from_env() == ROLE_COMBINED


def test_role_parsing_normalizes_case_and_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ROLE_ENV, "  HTTP ")
    assert host_role_from_env() == ROLE_HTTP


def test_role_parsing_accepts_all_valid_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for role in (ROLE_COMBINED, ROLE_HTTP, ROLE_SCHEDULER):
        monkeypatch.setenv(ROLE_ENV, role)
        assert host_role_from_env() == role


def test_invalid_role_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ROLE_ENV, "api")
    with pytest.raises(ValueError, match=ROLE_ENV):
        host_role_from_env()


def test_create_app_rejects_scheduler_role() -> None:
    from server.app.main import create_app

    with pytest.raises(RuntimeError, match="scheduler_process"):
        create_app(role=ROLE_SCHEDULER)


def test_create_prod_app_dispatches_roles(tmp_path, monkeypatch):
    """The uvicorn factory maps the env role onto the app composition:
    http starts no worker threads, combined (default) does. A regression
    flipping the ``start_worker`` boolean would otherwise pass the whole
    suite (create_app(role=...) without start_worker is threadless for
    every role)."""
    from fastapi.testclient import TestClient

    from server.app import main
    from server.app.events.agents import AgentStatusManager
    from server.app.workflow_worker.thread import WorkflowWorkerThread

    starts: list[str] = []
    monkeypatch.setattr(WorkflowWorkerThread, "start", lambda self: starts.append("w"))
    monkeypatch.setattr(AgentStatusManager, "discover", lambda self: [])
    monkeypatch.setattr(main, "validate_settings", lambda settings: None)
    for path_name in ["videos", "logs", "packages", "jobs"]:
        (tmp_path / path_name).mkdir(parents=True, exist_ok=True)
    real_load = main.load_settings

    def _load(*args, **kwargs):
        kwargs.setdefault("data_dir", tmp_path)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(main, "load_settings", _load)

    monkeypatch.setenv(ROLE_ENV, ROLE_HTTP)
    with TestClient(main.create_prod_app()) as _:
        pass
    assert starts == []

    monkeypatch.setenv(ROLE_ENV, ROLE_COMBINED)
    with TestClient(main.create_prod_app()) as _:
        pass
    assert starts == ["w"]


def test_probe_rejects_unknown_lock_name() -> None:
    from server.app.single_replica_probe import SingleReplicaProbe

    with pytest.raises(ValueError, match="lock name"):
        SingleReplicaProbe("postgresql://127.0.0.1:5432/x", lock_name="bogus")


def test_probe_lock_keys_are_disjoint_per_plane() -> None:
    from server.app.single_replica_probe import SingleReplicaProbe

    http_probe = SingleReplicaProbe("postgresql://127.0.0.1:5432/x", lock_name="control-plane-http")
    scheduler_probe = SingleReplicaProbe("postgresql://127.0.0.1:5432/x", lock_name="scheduler")
    assert http_probe._lock_key != scheduler_probe._lock_key
