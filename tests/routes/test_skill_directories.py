"""Skill directory listing route (Studio skill picker, #327)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Depends

import server.app.routes.skill_directories as skill_directories_routes
from server.app.auth.workspace_access import require_workspace_access


@pytest.fixture
def skills_base(tmp_path):
    base = tmp_path / "skills"
    (base / "ws-1" / "write-script").mkdir(parents=True)
    (base / "ws-1" / "review-questions").mkdir(parents=True)
    (base / "ws-1" / "notes.txt").write_text("not a dir", encoding="utf-8")
    (base / "ws-2" / "generate-questions").mkdir(parents=True)
    return base


def _mount(app, job_db) -> None:
    # routes/__init__.py is a shared file wired by the tower at merge time;
    # mount the router on a private app with the production auth dependency.
    # mount_spa's ``/api/{path:path}`` 404 guard is registered inside
    # create_app, so a plain include_router would land behind it and never
    # match: pop the guard, mount, then re-append it at the end.
    guard = next(
        route for route in app.router.routes if getattr(route, "path", None) == "/api/{path:path}"
    )
    app.router.routes.remove(guard)
    app.include_router(
        skill_directories_routes.create_skill_directories_router(job_db, app.state.settings),
        prefix="/api",
        dependencies=[Depends(require_workspace_access)],
    )
    app.router.routes.append(guard)


@pytest.fixture
def directories_client(client_factory, job_db, skills_base, monkeypatch):
    monkeypatch.setattr(
        skill_directories_routes,
        "build_skill_manager",
        lambda _dsn, _runs_dir=None: SimpleNamespace(base_dir=skills_base),
    )
    with client_factory(fresh=True, configure=lambda app: _mount(app, job_db)) as client:
        yield client


def test_directories_endpoint(directories_client) -> None:
    response = directories_client.get("/api/skills/directories", params={"scope": "ws-1"})

    assert response.status_code == 200
    assert response.json() == {
        "scope": "ws-1",
        "directories": ["review-questions", "write-script"],
    }


def test_directories_endpoint_isolates_scopes(directories_client) -> None:
    response = directories_client.get("/api/skills/directories", params={"scope": "ws-2"})

    assert response.status_code == 200
    assert response.json()["directories"] == ["generate-questions"]


def test_directories_endpoint_empty_for_unknown_scope(directories_client) -> None:
    response = directories_client.get("/api/skills/directories", params={"scope": "ws-nope"})

    assert response.status_code == 200
    assert response.json() == {"scope": "ws-nope", "directories": []}


def test_directories_endpoint_rejects_traversal_scope(directories_client) -> None:
    response = directories_client.get("/api/skills/directories", params={"scope": ".."})

    assert response.status_code == 200
    assert response.json()["directories"] == []


def test_directories_endpoint_requires_auth(client_factory, job_db) -> None:
    with client_factory(
        authenticated=False, fresh=True, configure=lambda app: _mount(app, job_db)
    ) as anon:
        response = anon.get("/api/skills/directories", params={"scope": "ws-1"})

    assert response.status_code == 401
