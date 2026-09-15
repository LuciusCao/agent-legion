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
from pathlib import Path

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


def test_get_with_corrupted_map_is_structured_422(client_factory, job_db, shared_home) -> None:
    """kimi review P2-6：损坏的 map.json 在 GET 侧也是结构化 422（agent 拿到
    可理解的错误定位坏文件），不是零信息 500。"""
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        shared = shared_home / "_shared"
        (shared / "references").mkdir(parents=True)
        (shared / "map.json").write_text("{ not json", encoding="utf-8")
        response = scoped.get(f"{_TOOLS}/workspaces/{_WS}/skills-shared")
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["errors"][0]["path"] == "map.json"


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


def test_put_rejects_duplicate_paths(client_factory, job_db, shared_home) -> None:
    """codex review R2 P1: two map.json entries must be a 422 BEFORE any
    write — the old loop validated only the first match while the write
    pass applied both, letting a malformed second copy through."""
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        response = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    {"path": "map.json", "content": "{ not json"},
                    {"path": "references/a.md", "content": "a"},
                    {"path": "references/a.md", "content": "b"},
                ]
            },
        )
        assert response.status_code == 422, response.text
        body = response.json()
        paths = {e["path"] for e in body["detail"]["errors"]}
        assert "map.json" in paths and "references/a.md" in paths
        # Nothing was written: the live dir stays absent.
        assert not (shared_home / "_shared").exists()


def test_put_full_state_replaces_and_removes_dropped_files(
    client_factory, job_db, shared_home
) -> None:
    """codex review R2 P2: the PUT is a FULL-STATE save — files omitted
    from the payload disappear (rename/retire of a material is otherwise
    impossible: the old loop only ever overwrote targets)."""
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        first = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    {"path": "references/prompt-style.md", "content": "v1"},
                    {"path": "references/old.md", "content": "obsolete"},
                ]
            },
        )
        assert first.status_code == 200, first.text
        # Second PUT drops references/old.md and renames the mapping.
        new_map = {
            "version": 1,
            "materials": [{"source": "references/style-v2.md", "skills": ["write-script"]}],
        }
        second = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(new_map)},
                    {"path": "references/style-v2.md", "content": "v2"},
                ]
            },
        )
        assert second.status_code == 200, second.text
        payload = second.json()
        assert [f["path"] for f in payload["files"]] == ["references/style-v2.md"]
        # The dropped material is gone from disk too, and no staging/retire
        # leftovers sit beside the live dir.
        shared = shared_home / "_shared"
        assert not (shared / "references" / "old.md").exists()
        assert not (shared / "references" / "prompt-style.md").exists()
        assert sorted(p.name for p in shared_home.iterdir()) == ["_shared"]


def test_put_staging_failure_leaves_previous_state_intact(
    client_factory, job_db, shared_home, monkeypatch
) -> None:
    """codex review R2 P1: a mid-write failure must not half-apply — the
    staged-swap writes everything into a temp dir first, so a failing
    write leaves the live dir exactly as it was. The operational IO error
    surfaces as SharedMaterialWriteError (unmapped JobServiceError → 500,
    the SkillGitError convention)."""
    from server.app.services.skill_shared_store import SharedMaterialWriteError
    from server.app.services.skill_shared_swap import write_shared_materials

    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        first = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    {"path": "references/prompt-style.md", "content": "v1"},
                ]
            },
        )
        assert first.status_code == 200, first.text

        shared = shared_home / "_shared"
        real_write = Path.write_text

        def failing_write(self, data, encoding=None, errors=None):
            # Fail only inside the staging dir (live-dir reads still work).
            if ".tmp-" in str(self):
                raise OSError("disk full")
            return real_write(self, data, encoding=encoding, errors=errors)

        monkeypatch.setattr(Path, "write_text", failing_write)
        with pytest.raises(SharedMaterialWriteError):
            write_shared_materials(
                shared,
                [("map.json", json.dumps(_MAP)), ("references/prompt-style.md", "v2-never")],
                shared_home,
            )
        monkeypatch.setattr(Path, "write_text", real_write)
        # The live dir is untouched: old content, no staging leftovers.
        assert (shared / "references" / "prompt-style.md").read_text(encoding="utf-8") == "v1"
        assert sorted(p.name for p in shared_home.iterdir()) == ["_shared"]


def test_put_rejects_content_over_the_utf8_byte_cap(client_factory, job_db, shared_home) -> None:
    """codex R3 P1: the wire cap counts CHARACTERS (max_length) but the
    disk/read cap counts BYTES — CJK content can pass the contract and
    still exceed 128 KiB, which the sync would silently truncate into
    every mapped skill. The PUT must reject it as 422."""
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        # 7 万个汉字 ≈ 21 万 UTF-8 字节 > 128 KiB，但字符数在 128K 上限内。
        big = "汉" * 70_000
        response = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    {"path": "references/prompt-style.md", "content": big},
                ]
            },
        )
        assert response.status_code == 422, response.text
        body = response.json()
        errors = body["detail"]["errors"]
        assert any("UTF-8 bytes" in e["error"] for e in errors)
        assert not (shared_home / "_shared").exists()


def test_put_rejects_map_sources_missing_from_the_full_state_payload(
    client_factory, job_db, shared_home
) -> None:
    """codex R3 P2: the PUT replaces the whole _shared directory — a map
    entry whose source file is not in the same payload would strand every
    mapped save_skill_version on "shared source unreadable" until the
    shared state is fixed. Reject with 422 naming the missing source."""
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        response = scoped.put(
            f"{_TOOLS}/workspaces/{_WS}/skills-shared",
            json={
                "files": [
                    {"path": "map.json", "content": json.dumps(_MAP)},
                    # references/prompt-style.md is mapped but absent.
                    {"path": "references/other.md", "content": "present"},
                ]
            },
        )
        assert response.status_code == 422, response.text
        body = response.json()
        errors = body["detail"]["errors"]
        assert any(
            "references/prompt-style.md" in e["error"] and "missing" in e["error"] for e in errors
        )
        assert not (shared_home / "_shared").exists()


def test_propagate_via_scoped_token(client_factory, job_db, shared_home, tmp_path) -> None:
    """#673: the MCP sync_shared_materials tool's endpoint — same guards as
    the rest of the surface, per-skill results, DB skill lock untouched."""
    import os
    import subprocess

    def git(repo: Path, *args: str) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        )
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        repo = shared_home / "write-script"
        repo.mkdir(parents=True)
        git(repo, "init", "-q")
        (repo / "references").mkdir()
        (repo / "scripts").mkdir()
        (repo / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        (repo / "references" / "output-contract.md").write_text("# c\n", encoding="utf-8")
        (repo / "references" / "prompt-style.md").write_text("# old\n", encoding="utf-8")
        (repo / "scripts" / "validate_output.py").write_text(
            "raise SystemExit(0)\n", encoding="utf-8"
        )
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
        git(repo, "tag", "v1.0.0")

        shared = shared_home / "_shared"
        (shared / "references").mkdir(parents=True)
        (shared / "map.json").write_text(json.dumps(_MAP), encoding="utf-8")
        (shared / "references" / "prompt-style.md").write_text("# new\n", encoding="utf-8")

        response = scoped.post(f"{_TOOLS}/workspaces/{_WS}/skills-shared/propagate", json={})
        assert response.status_code == 200, response.text
        (entry,) = response.json()["results"]
        assert entry["skill"] == "write-script"
        assert entry["status"] == "synced"
        assert entry["tag"] == "v1.0.1"

        # Unknown workspace 404; a token bound elsewhere 403.
        assert (
            scoped.post(
                f"{_TOOLS}/workspaces/ws-missing/skills-shared/propagate", json={}
            ).status_code
            == 404
        )


def test_get_skips_symlinked_material_root(client_factory, job_db, shared_home, tmp_path) -> None:
    """主 agent P1（#633 存量）：`_shared/references -> 外部目录` 时
    folder.is_dir() 跟随 symlink 且 rglob 会遍历根 symlink——scoped GET
    是唯一内联返回内容的读面，外部文件必须不可见（map 仍正常返回）。"""
    with client_factory(fresh=True) as client:
        _create_workspace(client)
        scoped = _scoped(client, job_db)
        shared = shared_home / "_shared"
        shared.mkdir(parents=True)
        (shared / "map.json").write_text(json.dumps(_MAP), encoding="utf-8")
        outside = tmp_path / "private"
        outside.mkdir()
        (outside / "prompt-style.md").write_text("smuggled\n", encoding="utf-8")
        (shared / "references").symlink_to(outside)

        payload = scoped.get(f"{_TOOLS}/workspaces/{_WS}/skills-shared").json()
        assert payload["map"] == _MAP
        assert payload["files"] == []
