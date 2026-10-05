"""GET /api/agent-workers membership narrowing (#752).

Admins keep the full listing; non-admin identities only see workers whose
admission scope intersects their member workspaces, with the scope trimmed
and the cross-workspace key binding withheld.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server.app.routes.agent_worker_listing import narrow_workers_to_visible
from tests.helpers.agent_worker_api import (
    authenticate_admin,
    issue_scoped_token,
    make_app,
    register,
)


def _member_client(admin: TestClient, username: str, workspace_ids: list[str]) -> TestClient:
    created = admin.post("/api/users", json={"username": username, "password": "pw"})
    assert created.status_code == 201, created.text
    for workspace_id in workspace_ids:
        bound = admin.put(
            f"/api/workspaces/{workspace_id}/members",
            json={"user_id": created.json()["id"], "role": "viewer"},
        )
        assert bound.status_code == 200, bound.text
    member = TestClient(admin.app)
    login = member.post("/api/auth/login", json={"username": username, "password": "pw"})
    assert login.status_code == 200, login.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _workers_by_id(client: TestClient, **params: str) -> dict[str, dict]:
    response = client.get("/api/agent-workers", params=params)
    assert response.status_code == 200, response.text
    return {worker["worker_id"]: worker for worker in response.json()["workers"]}


def test_listing_is_narrowed_to_member_workspaces(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        token_a = issue_scoped_token(client, "ws-a")
        token_b = issue_scoped_token(client, "ws-b")
        register(client, token_a, worker_id="only-a")
        register(client, token_b, worker_id="only-b")
        register(client, token_a, worker_id="both", tokens=[token_a, token_b])
        authenticate_admin(client)

        admin_view = _workers_by_id(client)
        assert set(admin_view) == {"only-a", "only-b", "both"}
        assert sorted(admin_view["both"]["allowed_workspaces"]) == ["ws-a", "ws-b"]
        assert admin_view["both"]["register_token_ids"]

        member = _member_client(client, "member-a", ["ws-a"])
        member_view = _workers_by_id(member)
        assert set(member_view) == {"only-a", "both"}
        assert member_view["both"]["allowed_workspaces"] == ["ws-a"]
        assert all(worker["register_token_ids"] == [] for worker in member_view.values())

        # A workspace narrowing outside the caller's membership yields
        # nothing rather than the other workspace's workers.
        assert _workers_by_id(member, workspace_id="ws-b") == {}
        assert set(_workers_by_id(member, workspace_id="ws-a")) == {"only-a", "both"}

        outsider = _member_client(client, "no-membership", [])
        assert _workers_by_id(outsider) == {}


def test_narrowing_keeps_unrestricted_rows_and_hides_legacy_scope() -> None:
    workers = [
        {"worker_id": "legacy", "allowed_workspaces": [], "register_token_ids": []},
        {"worker_id": "a", "allowed_workspaces": ["ws-a", "ws-x"], "register_token_ids": ["t"]},
    ]
    assert narrow_workers_to_visible(workers, None) == workers
    narrowed = narrow_workers_to_visible(workers, frozenset({"ws-a"}))
    assert narrowed == [
        {"worker_id": "a", "allowed_workspaces": ["ws-a"], "register_token_ids": []}
    ]
    # The input rows are not mutated (registry payloads may be reused).
    assert workers[1]["allowed_workspaces"] == ["ws-a", "ws-x"]
