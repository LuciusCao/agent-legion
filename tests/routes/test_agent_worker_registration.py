"""Agent Worker registration contract tests: scoped tokens, scope storage,
the worker↔token binding (schema v59), and registration record deletion.

Shared registration helpers live in tests/helpers/agent_worker_api.py (PR #181
extracted them for the whole agent worker / broker test surface); this file
keeps only the registration-contract scenarios.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.agent_register_key_guard import RegisterKeyDeleted
from tests.helpers.agent_worker_api import (
    authenticate_admin,
    issue_scoped_token,
    make_app,
    register,
    seed_request,
)

_authenticate_admin = authenticate_admin
_issue_scoped_token = issue_scoped_token
_make_app = make_app
_register = register
_seed_request = seed_request


def test_register_with_scoped_token_stores_and_returns_scope(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-1", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
        scoped = _issue_scoped_token(client)
        other = _issue_scoped_token(client, workspace_id="other-workspace")

        scoped_registration = _register(client, credential=scoped, worker_id="scoped-worker")
        assert scoped_registration["allowed_workspaces"] == ["test-workspace"]
        # issue #35: the response carries workspace rows (id + name + the token
        # ids that opened it) so the Worker console can label each token.
        workspaces_row = scoped_registration["workspaces"]
        assert [row["workspace_id"] for row in workspaces_row] == ["test-workspace"]
        assert all(row["workspace_name"] for row in workspaces_row)
        assert workspaces_row[0]["token_ids"] == [scoped.partition(".")[0]]

        listed = client.get("/api/agent-workers")
        assert listed.status_code == 200
        workers = {w["worker_id"]: w for w in listed.json()["workers"]}
        assert workers["scoped-worker"]["allowed_workspaces"] == ["test-workspace"]

        # Multiple tokens in one registration merge their scopes (union).
        merged = _register(
            client,
            credential=None,
            worker_id="scoped-worker",
            tokens=[scoped, other],
        )
        assert sorted(merged["allowed_workspaces"]) == [
            "other-workspace",
            "test-workspace",
        ]
        # 每个 workspace 行记录开通它的 token id，控制台按 token_id 关联卡片。
        by_workspace = {row["workspace_id"]: row["token_ids"] for row in merged["workspaces"]}
        assert by_workspace["test-workspace"] == [scoped.partition(".")[0]]
        assert by_workspace["other-workspace"] == [other.partition(".")[0]]

        # The workspace view only sees workers scoped to it; the legacy []
        # scope (a retired global registration) would be invisible there.
        marketing_view = client.get(
            "/api/agent-workers", params={"workspace_id": "test-workspace"}
        ).json()["workers"]
        assert [w["worker_id"] for w in marketing_view] == ["scoped-worker"]


def test_register_records_worker_token_binding(tmp_path: Path) -> None:
    """The worker↔key binding (schema v59) is stored at registration and
    refreshed on every re-registration."""
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
        first = _issue_scoped_token(client)
        _register(client, credential=first)

        workers = client.get("/api/agent-workers").json()["workers"]
        assert workers[0]["register_token_ids"] == [first.partition(".")[0]]

        # Re-registering with a different token re-points the binding.
        second = _issue_scoped_token(client)
        _register(client, credential=second)
        workers = client.get("/api/agent-workers").json()["workers"]
        assert workers[0]["register_token_ids"] == [second.partition(".")[0]]

        # Multiple tokens in one registration bind all of them.
        _register(client, credential=None, tokens=[first, second])
        workers = client.get("/api/agent-workers").json()["workers"]
        assert workers[0]["register_token_ids"] == sorted(
            [first.partition(".")[0], second.partition(".")[0]]
        )


def test_delete_worker_blocked_while_bound_key_is_alive(tmp_path: Path) -> None:
    """Worker records are deleted manually only for binding-less legacy rows:
    with a live bound key the delete is blocked, and deleting the key itself
    cascade-removes the record."""
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
        credential = _issue_scoped_token(client)
        _register(client, credential=credential)
        token_id = credential.partition(".")[0]

        # While the bound key is alive the record cannot be deleted.
        live = client.delete("/api/agent-workers/home-mini")
        assert live.status_code == 409
        missing = client.delete("/api/agent-workers/ghost")
        assert missing.status_code == 404

        # Deleting the key cascade-deletes the record in one transaction.
        deleted_key = client.delete(f"/api/agent-register-tokens/{token_id}")
        assert deleted_key.status_code == 200
        assert deleted_key.json()["cascaded_worker_ids"] == ["home-mini"]

        workers = client.get("/api/agent-workers").json()["workers"]
        assert workers == []
        # The record is already gone — manual deletion reports 404.
        assert client.delete("/api/agent-workers/home-mini").status_code == 404


def test_delete_key_narrows_multi_key_worker_to_surviving_scope(
    tmp_path: Path,
) -> None:
    """A Worker holding several keys keeps its record when one key is
    deleted; the dead binding is pruned and its stored scope narrows to the
    surviving keys' workspaces, atomically."""
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        _authenticate_admin(client)
        first = _issue_scoped_token(client)
        second = _issue_scoped_token(client, workspace_id="other-workspace")
        _register(client, credential=None, tokens=[first, second])
        first_id = first.partition(".")[0]
        second_id = second.partition(".")[0]

        deleted_key = client.delete(f"/api/agent-register-tokens/{first_id}")
        assert deleted_key.status_code == 200
        assert deleted_key.json()["cascaded_worker_ids"] == []

        workers = client.get("/api/agent-workers").json()["workers"]
        assert len(workers) == 1
        assert workers[0]["register_token_ids"] == [second_id]
        assert workers[0]["allowed_workspaces"] == ["other-workspace"]

        # Deleting the last live key cascades the record away.
        deleted_last = client.delete(f"/api/agent-register-tokens/{second_id}")
        assert deleted_last.json()["cascaded_worker_ids"] == ["home-mini"]
        assert client.get("/api/agent-workers").json()["workers"] == []


def test_issue_token_aborts_when_bound_key_is_deleted(tmp_path: Path) -> None:
    """SECURITY-WORKER-001: the admission keys are revalidated inside the
    write transaction, so a key deleted between the route's read-only
    resolve and the worker write aborts the registration — the cascade
    cannot see an uncommitted row, so without this guard a worker_token
    bound to a dead key would survive."""
    app = _make_app(tmp_path)
    registry = app.state.agent_worker_registry

    with TestClient(app) as client:
        _authenticate_admin(client)
        credential = _issue_scoped_token(client)
        token_id = credential.partition(".")[0]

    # Simulate the race: the credential resolved fine, the key died before
    # the worker row was written (route-level delete + registry-level retry).
    cascaded = registry.delete_register_token(token_id)
    assert cascaded == []

    with pytest.raises(RegisterKeyDeleted, match="no longer exists"):
        registry.issue_token(
            worker_id="racy-worker",
            name="Racy",
            runtimes=["pi"],
            max_concurrency=1,
            register_token_ids=[token_id],
        )
    # The write transaction rolled back: no worker row survived.
    assert all(w["worker_id"] != "racy-worker" for w in registry.list_workers())


def test_legacy_worker_without_recorded_binding_is_always_deletable(
    tmp_path: Path,
) -> None:
    """Pre-v59 registrations have no recorded key binding; they are the
    migration cleanup target and can be deleted directly."""
    app = _make_app(tmp_path)
    registry = app.state.agent_worker_registry
    registry.issue_token(
        worker_id="legacy-worker",
        name="Legacy",
        runtimes=["pi"],
        max_concurrency=1,
    )
    assert registry.delete_worker("legacy-worker") == "deleted"
    assert registry.delete_worker("legacy-worker") == "not_found"
