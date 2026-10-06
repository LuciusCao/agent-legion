"""Scoped-token workspace binding × every guarded workspace-scoped GET (#971).

``test_job_idor_matrix`` (#710) pins the job-id routes; this file pins the
other half: every GET route whose authorization scope is a ``workspace_id``
(path parameter, or the query-parameter fallback) and that mounts one of the
two membership guards must refuse a workspace-bound scoped token addressing
a foreign workspace — for both minter shapes the guards used to let through:

- an admin minter (the guard's admin fast path ran before any binding check);
- a non-admin member of BOTH workspaces (membership passes on the foreign
  workspace, so only the binding can refuse).

The route set is enumerated from the live app rather than hand-listed, so a
new secured GET joins the matrix automatically (the #631-style drift where a
new endpoint ships outside the hand-copied list). The refusal must come from
the guard itself — the uniform 403 "bound" detail, decided before any
handler or DB read — not from an incidental 404 on dummy path values.
"""

from __future__ import annotations

import re

import pytest
from fastapi.exceptions import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from server.app.auth import scoped_tokens
from server.app.auth.scope_binding import BOUND_ELSEWHERE_DETAIL
from server.app.auth.workspace_access import (
    require_job_workspace_access,
    require_workspace_access,
)

CSRF = {"x-agent-legion-request": "1"}
_GUARDS = (require_workspace_access, require_job_workspace_access)
_HOME = "ws_bind_home"
_FOREIGN = "ws_bind_foreign"


def _dependency_calls(route: APIRoute) -> set:
    calls = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        calls.add(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def _is_event_stream(route: APIRoute) -> bool:
    return any(
        "text/event-stream" in (spec.get("content") or {})
        for spec in (route.responses or {}).values()
    )


def _guarded_workspace_get_routes(app, *, streams: bool = False) -> list[tuple[str, bool]]:
    """``(path template, scope-in-query)`` for each guarded workspace GET.

    Job-id routes are excluded: their scope comes from the job row and the
    binding refusal there is the job-not-found 404 (test_job_idor_matrix).
    SSE routes are split out (``streams=True``): over HTTP a regression
    there would open an endless stream and hang the test instead of
    failing it, so they are checked against the guard directly.
    """
    found: list[tuple[str, bool]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if not _dependency_calls(route) & set(_GUARDS):
            continue
        if _is_event_stream(route) != streams:
            continue
        params = set(re.findall(r"{([^}:]+)", route.path))
        if "job_id" in params:
            continue
        if "workspace_id" in params:
            found.append((route.path, False))
        elif any(p.name == "workspace_id" for p in route.dependant.query_params):
            found.append((route.path, True))
    return sorted(found)


def _concrete(path: str, workspace_id: str) -> str:
    path = path.replace("{workspace_id}", workspace_id)
    return re.sub(r"{[^}]+}", "probe", path)


def _bound_client(client, user_id: str):
    token = scoped_tokens.mint_scoped_token(client.app.state.job_db, user_id, workspace_id=_HOME)
    bound = client.__class__(client.app)
    bound.headers["authorization"] = f"Bearer {token}"
    bound.identity = client.app.state.auth_service.authenticate_scoped(token)
    return bound


@pytest.fixture
def bound_minters(client):
    """Two workspace-bound tokens (bound to _HOME): admin-minted and
    minted by a member of both workspaces."""
    for workspace_id in (_HOME, _FOREIGN):
        created = client.post(
            "/api/workspaces", json={"id": workspace_id, "name": workspace_id}, headers=CSRF
        )
        assert created.status_code in (200, 201), created.text
    job_db = client.app.state.job_db
    member = client.post(
        "/api/users",
        json={"username": "bind-dual", "password": "bind-dual-password"},
        headers=CSRF,
    )
    assert member.status_code == 201, member.text
    member_id = member.json()["id"]
    job_db.upsert_workspace_member(_HOME, member_id, "viewer")
    job_db.upsert_workspace_member(_FOREIGN, member_id, "viewer")
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    return {
        "admin": _bound_client(client, admin_id),
        "dual-member": _bound_client(client, member_id),
    }


def test_matrix_enumerates_the_known_guarded_surfaces(client) -> None:
    """Guard against the enumeration silently collapsing (a wiring change
    that hides the guards from the dependant tree would empty the matrix
    and pass vacuously)."""
    routes = dict(_guarded_workspace_get_routes(client.app))
    assert len(routes) >= 30, routes
    # Representative members of each surface class the review named.
    assert "/api/workspaces/{workspace_id}/secrets" in routes
    assert "/api/workspaces/{workspace_id}/jobs/snapshot" in routes
    assert any(scope_in_query for scope_in_query in routes.values())


@pytest.mark.parametrize("minter", ["admin", "dual-member"])
def test_bound_token_is_refused_on_every_foreign_workspace_get(
    client, bound_minters, minter
) -> None:
    bound = bound_minters[minter]
    leaks: list[tuple[str, int, object]] = []
    for path, scope_in_query in _guarded_workspace_get_routes(client.app):
        if scope_in_query:
            response = bound.get(_concrete(path, _FOREIGN), params={"workspace_id": _FOREIGN})
        else:
            response = bound.get(_concrete(path, _FOREIGN))
        refused = response.status_code == 403
        detail = response.json().get("detail") if refused else None
        if not refused or detail != BOUND_ELSEWHERE_DETAIL:
            leaks.append((path, response.status_code, detail))
    assert leaks == []


@pytest.mark.parametrize("minter", ["admin", "dual-member"])
def test_bound_token_is_refused_on_foreign_workspace_streams(client, bound_minters, minter) -> None:
    """The SSE half of the matrix, driven through each route's own guard."""
    identity = bound_minters[minter].identity
    streams = _guarded_workspace_get_routes(client.app, streams=True)
    assert streams, "the workspace event streams vanished from the matrix"
    for path, scope_in_query in streams:
        route = next(r for r in client.app.routes if getattr(r, "path", None) == path)
        guard = next(g for g in _GUARDS if g in _dependency_calls(route))
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": _concrete(path, _FOREIGN),
                "path_params": {} if scope_in_query else {"workspace_id": _FOREIGN},
                "query_string": f"workspace_id={_FOREIGN}".encode() if scope_in_query else b"",
                "headers": [],
                "app": client.app,
                "route": route,
            }
        )
        with pytest.raises(HTTPException) as refused:
            guard(request=request, user=identity)
        assert (refused.value.status_code, refused.value.detail) == (
            403,
            BOUND_ELSEWHERE_DETAIL,
        ), path


@pytest.mark.parametrize("minter", ["admin", "dual-member"])
def test_bound_token_keeps_its_own_workspace(client, bound_minters, minter) -> None:
    """No regression: the binding narrows, it never locks the token out of
    the workspace it is bound to."""
    bound = bound_minters[minter]
    assert bound.get(f"/api/workspaces/{_HOME}/secrets").status_code == 200
    assert bound.get(f"/api/workspaces/{_HOME}/jobs/snapshot").status_code == 200
    assert bound.get(f"/api/workspaces/{_FOREIGN}/secrets").status_code == 403


def test_full_sessions_keep_their_cross_workspace_reach(client, bound_minters) -> None:
    """The binding is a property of the token, not the user: the admin
    session and the member's own session still read both workspaces."""
    for workspace_id in (_HOME, _FOREIGN):
        assert client.get(f"/api/workspaces/{workspace_id}/secrets").status_code == 200
    member = client.__class__(client.app)
    login = member.post(
        "/api/auth/login", json={"username": "bind-dual", "password": "bind-dual-password"}
    )
    assert login.status_code == 200, login.text
    for workspace_id in (_HOME, _FOREIGN):
        assert member.get(f"/api/workspaces/{workspace_id}/jobs/snapshot").status_code == 200
