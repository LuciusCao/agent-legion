"""Scoped register token issuance/registration behavior tests (issue #35).

Split from test_agent_workers.py to stay under the test-file line budget;
helpers (_make_app, _register, _issue_scoped_token, _authenticate_admin,
_seed_request) are imported from the parent suite.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.helpers.agent_worker_api import (
    authenticate_admin as _authenticate_admin,
)
from tests.helpers.agent_worker_api import (
    issue_scoped_token as _issue_scoped_token,
)
from tests.helpers.agent_worker_api import (
    make_app as _make_app,
)
from tests.helpers.agent_worker_api import (
    register as _register,
)
from tests.helpers.agent_worker_api import (
    seed_request as _seed_request,
)


def test_register_token_requires_workspace(tmp_path: Path) -> None:
    """issue #35：签发时 workspace 必填，「全部 workspace」token 停止签发。"""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-regtokens-1", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
        missing = client.post(
            "/api/agent-register-tokens", json={"workspace_id": None, "label": "x"}
        )
        assert missing.status_code == 422
        empty = client.post("/api/agent-register-tokens", json={"workspace_id": "", "label": "x"})
        assert empty.status_code == 422


def test_registration_with_one_bad_token_fails_whole_call(tmp_path: Path) -> None:
    """issue #35：多 token 注册时任一 token 无效即整单 401（scope 不会被静默缩小）。"""
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-regtokens-2", limit=2)

    with TestClient(app) as client:
        _authenticate_admin(client)
        good = _issue_scoped_token(client)
        response = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Tokens": f"{good},bogus-token"},
            json={"worker_id": "bad-token-w1", "runtimes": ["pi"], "max_concurrency": 1},
        )
        assert response.status_code == 401
        listed = client.get("/api/agent-workers").json()["workers"]
        assert all(w["worker_id"] != "bad-token-w1" for w in listed)


def test_agent_register_token_management_api(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-regtokens-3", limit=2)

    # Anonymous calls and the legacy register-token header both get 401
    # (SECURITY-AUTH-001) — asserted on a cookie-less client.
    with TestClient(app) as anonymous:
        unauthenticated = anonymous.post(
            "/api/agent-register-tokens",
            json={"workspace_id": "test-workspace", "label": "no header"},
        )
        assert unauthenticated.status_code == 401
        legacy = anonymous.post(
            "/api/agent-register-tokens",
            headers={"X-Agent-Worker-Register-Token": "nope"},
            json={"workspace_id": "test-workspace", "label": "legacy header"},
        )
        assert legacy.status_code == 401

    with TestClient(app) as client:
        _authenticate_admin(client)

        created = client.post(
            "/api/agent-register-tokens",
            json={"workspace_id": "test-workspace", "label": "home mini"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["workspace_id"] == "test-workspace"
        assert body["label"] == "home mini"
        plaintext = body["register_token"]
        assert plaintext.startswith(f"{body['token_id']}.")

        # The list view never carries secret material.
        listed = client.get("/api/agent-register-tokens")
        assert listed.status_code == 200
        entry = next(t for t in listed.json()["tokens"] if t["token_id"] == body["token_id"])
        assert entry["workspace_id"] == "test-workspace"
        assert entry["revoked"] is False
        assert "token_hash" not in entry
        assert "register_token" not in entry

        # A scoped token for a nonexistent workspace is rejected.
        missing = client.post(
            "/api/agent-register-tokens",
            json={"workspace_id": "no-such-workspace"},
        )
        assert missing.status_code == 400

        # Revoke kills the credential.
        revoked = client.post(f"/api/agent-register-tokens/{body['token_id']}/revoke")
        assert revoked.status_code == 200
        assert revoked.json() == {"revoked": True}
        register_after_revoke = client.post(
            "/api/agent-workers/register",
            headers={"X-Agent-Worker-Register-Token": plaintext},
            json={"worker_id": "w1", "runtimes": ["pi"], "max_concurrency": 1},
        )
        assert register_after_revoke.status_code == 401
        unknown = client.post("/api/agent-register-tokens/no-such/revoke")
        assert unknown.status_code == 404


def test_agent_worker_revoke_api(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _seed_request(app.state.job_db, job_id="job-regtokens-4", limit=2)

    # Anonymous calls and the legacy register-token header both get 401
    # (SECURITY-AUTH-001) — asserted on a cookie-less client.
    with TestClient(app) as anonymous:
        unauthenticated = anonymous.post("/api/agent-workers/no-such/revoke")
        assert unauthenticated.status_code == 401
        legacy = anonymous.post(
            "/api/agent-workers/no-such/revoke",
            headers={"X-Agent-Worker-Register-Token": "nope"},
        )
        assert legacy.status_code == 401

    with TestClient(app) as client:
        _authenticate_admin(client)

        token = _register(client)["worker_token"]
        revoked = client.post("/api/agent-workers/home-mini/revoke")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json() == {"worker_id": "home-mini", "revoked": True}

        # The revoked Worker's credential is dead: authenticate rejects it, so
        # the claim poll fails before ever reaching the broker.
        claim = client.post(
            "/api/agent-executions/claim",
            headers={"X-Agent-Worker-Token": token},
            json={"worker_id": "home-mini"},
        )
        assert claim.status_code in (401, 409)

        listed = client.get("/api/agent-workers").json()["workers"]
        mine = next(w for w in listed if w["worker_id"] == "home-mini")
        assert mine["revoked"] is True

        # Re-revoke is idempotent: the row still exists, so it succeeds again.
        again = client.post("/api/agent-workers/home-mini/revoke")
        assert again.status_code == 200
        assert again.json() == {"worker_id": "home-mini", "revoked": True}
