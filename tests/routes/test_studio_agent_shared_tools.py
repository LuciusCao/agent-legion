"""Studio-agent shared skill material endpoints (#633).

GET/PUT ``/api/studio-agent/tools/workspaces/{id}/skills-shared``: the
structured empty state, populated reads, full-state writes with
validate-everything-first (bad map.json 422, path escape 422, out-of-bounds
422), 404 on unknown workspaces, and the workspace binding of run tokens.
The ``skill_home`` pattern (real git repos under a monkeypatched HOME)
comes from test_studio_agent_skill_tools.py; here it only needs the
workspace skill DIR, since `_shared` is not a git repo.
"""

from __future__ import annotations

import json

import pytest

from server.app.auth import scoped_tokens

_TOOLS = "/api/studio-agent/tools"
_WS = "education-video-problems-generation"


def _create_workspace(client, name: str = "Shared Materials WS") -> str:
    """Create the workspace whose id doubles as the skills-root dir name."""
    response = client.post("/api/workspaces", json={"id": _WS, "name": name})
    assert response.status_code == 200, response.text
    return _WS


_MAP = {
    "version": 1,
    "materials": [{"source": "references/prompt-style.md", "skills": ["write-script"]}],
}


@pytest.fixture
def shared_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ws_dir = home / ".agents" / "skills" / _WS
    ws_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return ws_dir


def _scoped(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped_client = client.__class__(client.app)
    scoped_client.headers["authorization"] = f"Bearer {token}"
    return scoped_client


def _workspace_id(client) -> str:
    return _WS  # the demo workspace id doubles as the skills-root dir name


def test_get_empty_state_and_populated(client_factory, job_db, shared_home) -> None:
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        empty = scoped.get(f"{_TOOLS}/workspaces/{_WS}/skills-shared")
        assert empty.status_code == 200, empty.text
        assert empty.json() == {"workspace_id": _WS, "map": None, "files": []}

        shared = shared_home / "_shared"
        (shared / "references").mkdir(parents=True)
        (shared / "map.json").write_text(json.dumps(_MAP), encoding="utf-8")
        (shared / "references" / "prompt-style.md").write_text("# style\n", encoding="utf-8")

        populated = scoped.get(f"{_TOOLS}/workspaces/{_WS}/skills-shared")
        assert populated.status_code == 200, populated.text
        payload = populated.json()
        assert payload["map"] == _MAP
        assert [f["path"] for f in payload["files"]] == ["references/prompt-style.md"]
        assert payload["files"][0]["content"] == "# style\n"
        assert payload["files"][0]["truncated"] is False


def test_put_writes_and_round_trips(client_factory, job_db, shared_home) -> None:
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        files = [
            {"path": "map.json", "content": json.dumps(_MAP)},
            {"path": "references/prompt-style.md", "content": "# house style\n"},
        ]
        saved = scoped.put(f"{_TOOLS}/workspaces/{_WS}/skills-shared", json={"files": files})
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        assert payload["map"] == _MAP
        assert [f["path"] for f in payload["files"]] == ["references/prompt-style.md"]
        # The response shape equals the post-write GET.
        reread = scoped.get(f"{_TOOLS}/workspaces/{_WS}/skills-shared")
        assert reread.json() == payload


def test_put_rejects_bad_map_json(client_factory, job_db, shared_home) -> None:
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        for bad_map in ("{not json", '{"version": 2, "materials": []}', '{"version": 1}'):
            response = scoped.put(
                f"{_TOOLS}/workspaces/{_WS}/skills-shared",
                json={"files": [{"path": "map.json", "content": bad_map}]},
            )
            assert response.status_code == 422, bad_map
            assert response.json()["detail"]["errors"]
        # A schema-valid map with a bad material is also a 422.
        bad_material = {"version": 1, "materials": [{"source": "docs/x.md", "skills": ["a"]}]}
        response = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={"files": [{"path": "map.json", "content": json.dumps(bad_material)}]},
        )
        assert response.status_code == 422
        # Nothing was written (the material dir would have been created).
        assert not (shared_home / "_shared").exists()


def test_put_rejects_escaping_and_out_of_bounds_paths(client_factory, job_db, shared_home) -> None:
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        escape = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    {"path": "../escape.md", "content": "x"},
                ]
            },
        )
        assert escape.status_code == 422
        assert escape.json()["detail"]["errors"][0]["path"] == "../escape.md"

        root_file = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    {"path": "loose.md", "content": "x"},  # root allows only map.json
                ]
            },
        )
        assert root_file.status_code == 422

        oversized = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    {"path": "references/big.md", "content": "x" * (128 * 1024 + 1)},
                ]
            },
        )
        assert oversized.status_code == 422

        too_many = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={"files": [{"path": f"references/f{i}.md", "content": "x"} for i in range(101)]},
        )
        assert too_many.status_code == 422
        assert not (shared_home / "_shared").exists()


def test_unknown_workspace_is_404(client_factory, job_db, shared_home) -> None:
    del shared_home
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        assert scoped.get(f"{_TOOLS}/workspaces/ws-missing/skills-shared").status_code == 404
        response = scoped.put(
            f"{_TOOLS}/workspaces/ws-missing/skills-shared",
            json={"files": [{"path": "map.json", "content": json.dumps(_MAP)}]},
        )
        assert response.status_code == 404


def test_workspace_bound_token_cannot_write_other_workspace(
    client_factory, job_db, shared_home
) -> None:
    with client_factory(fresh=True) as client:
        admin_id = str(job_db.get_user_credentials("admin")["id"])
        other = job_db.create_workspace(
            "Other WS", default_workflow_key="other_ws_flow", workspace_id="other_ws_flow"
        )
        bound_token = scoped_tokens.mint_scoped_token(
            job_db, admin_id, workspace_id=str(other["id"])
        )
        bound = client.__class__(client.app)
        bound.headers["authorization"] = f"Bearer {bound_token}"
        # The run token bound to the other workspace cannot touch _WS's shared materials.
        assert bound.get(f"{_TOOLS}/workspaces/{_WS}/skills-shared").status_code == 403
        assert (
            bound.put(
                f"{_TOOLS}/workspaces/{_WS}/skills-shared",
                json={"files": [{"path": "map.json", "content": json.dumps(_MAP)}]},
            ).status_code
            == 403
        )
