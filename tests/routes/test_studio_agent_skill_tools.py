"""Studio-agent skill tool endpoints
(/api/studio-agent/tools/workspaces/{id}/skills/*, #217 — workspace-scoped
since #710: skills are workspace-isolated, matching create_skill #633).

Scoped tokens get read/validate/save-version over the LOCAL skill repos;
full user sessions are refused at the scope guard (see
test_studio_agent_tools.py for the 401/403 inventory). save_skill_version
commits + tags the in-place repo but never touches the DB skill lock.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from server.app.auth import scoped_tokens
from server.app.services.skill_lock_store import SkillLockStore
from server.app.skills.config import LockedSkill, SkillsLock

_WS = "education-video-problems-generation"
_KEY = f"{_WS}/write-script"
_TOOLS = f"/api/studio-agent/tools/workspaces/{_WS}/skills"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _make_skill_repo(repo: Path, tag: str = "v1.0.0") -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "SKILL.md").write_text("# Write Script\n", encoding="utf-8")
    (repo / "references").mkdir()
    (repo / "references" / "output-contract.md").write_text("# contract\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "validate_output.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    # #542: the machine contract lives in the skill root.
    (repo / "contract.yaml").write_text(
        "files:\n  - path: script.md\n    format: text\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    _git(repo, "tag", tag)


@pytest.fixture
def skill_home(tmp_path, monkeypatch, job_db):
    base = tmp_path / "home" / ".agents" / "skills"
    _make_skill_repo(base / _KEY)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # The endpoints are workspace-bound (#710): the skill key's first
    # segment must be a real workspace the token is bound to.
    job_db.create_workspace(_WS, default_workflow_key=_WS, workspace_id=_WS)
    return base / _KEY


def _scoped(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=_WS)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped


def test_get_skill_and_ref_preview(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        latest = scoped.get(f"{_TOOLS}/{_KEY}")
        assert latest.status_code == 200, latest.text
        # #322: the default detail is the working tree at HEAD (``latest``).
        assert latest.json()["ref"] == "latest"
        assert latest.json()["commit"] == _git(skill_home, "rev-parse", "HEAD")
        assert latest.json()["available"] is True
        assert latest.json()["tags"] == ["v1.0.0"]
        assert any(f["path"] == "SKILL.md" for f in latest.json()["files"])

        tagged = scoped.get(f"{_TOOLS}/{_KEY}", params={"ref": "v1.0.0"})
        assert tagged.status_code == 200, tagged.text
        assert tagged.json()["ref"] == "v1.0.0"
        assert tagged.json()["tags"] == ["v1.0.0"]
        assert tagged.json()["commit"] == _git(skill_home, "rev-parse", "v1.0.0^{commit}")

        missing = scoped.get(f"{_TOOLS}/{_KEY}", params={"ref": "v9.9.9"})
        assert missing.status_code == 404


def test_preview_endpoint_ref_param(client_factory, skill_home) -> None:
    # The Studio panel surface shares the implementation with the MCP read.
    with client_factory(fresh=True) as client:
        tagged = client.get(
            f"/api/agent-catalog/skills/{_KEY}",
            params={"ref": "v1.0.0", "workspace_id": _WS},
        )
        assert tagged.status_code == 200, tagged.text
        assert tagged.json()["ref"] == "v1.0.0"
        assert (
            client.get(
                f"/api/agent-catalog/skills/{_KEY}",
                params={"ref": "nope", "workspace_id": _WS},
            ).status_code
            == 404
        )


def test_validate_skill(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        ok = scoped.post(f"{_TOOLS}/{_KEY}/validate")
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"key": _KEY, "valid": True, "errors": [], "warnings": []}

        (skill_home / "scripts" / "validate_output.py").unlink()
        broken = scoped.post(f"{_TOOLS}/{_KEY}/validate")
        assert broken.status_code == 200
        assert broken.json()["valid"] is False
        assert broken.json()["errors"] == [
            {"path": "scripts/validate_output.py", "error": "missing scripts/validate_output.py"}
        ]


def test_save_skill_version_commits_tags_and_keeps_lock(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        before = _git(skill_home, "rev-parse", "HEAD")
        saved = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": "SKILL.md", "content": "# Write Script v2\n"}],
                "new_tag": "v1.1.0",
                "message": "revise write-script",
            },
        )
        assert saved.status_code == 201, saved.text
        payload = saved.json()
        assert payload["tag"] == "v1.1.0"
        assert payload["commit"] == _git(skill_home, "rev-parse", "HEAD")
        assert payload["commit"] != before
        assert payload["synced_files"] == []  # no _shared dir: nothing synced
        assert _git(skill_home, "log", "-1", "--format=%an <%ae>") == (
            "agent-legion-studio <studio@local>"
        )
        # The lock is untouched (no relock happened); the default detail now
        # follows HEAD (``latest``), and the new tag previews via ?ref=.
        store = SkillLockStore(job_db.dsn_identity)
        assert (store.get_lock() or SkillsLock()).skills == {}
        detail = scoped.get(f"{_TOOLS}/{_KEY}")
        assert detail.json()["ref"] == "latest"
        preview = scoped.get(f"{_TOOLS}/{_KEY}", params={"ref": "v1.1.0"})
        skill_md = next(f for f in preview.json()["files"] if f["path"] == "SKILL.md")
        assert skill_md["content"] == "# Write Script v2\n"


def test_save_skill_version_syncs_shared_materials(client_factory, job_db, skill_home) -> None:
    """#633 end-to-end: a mapped _shared material lands in the skill repo's
    new commit and is reported in synced_files; a hand-supplied copy of the
    mapped path is rejected with 422 naming it."""
    shared = skill_home.parent / "_shared"
    (shared / "references").mkdir(parents=True)
    (shared / "map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "materials": [{"source": "references/prompt-style.md", "skills": ["write-script"]}],
            }
        ),
        encoding="utf-8",
    )
    (shared / "references" / "prompt-style.md").write_text("# house style\n", encoding="utf-8")

    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        saved = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": "SKILL.md", "content": "# Write Script v2\n"}],
                "new_tag": "v1.1.0",
                "message": "revise",
            },
        )
        assert saved.status_code == 201, saved.text
        assert saved.json()["synced_files"] == ["references/prompt-style.md"]
        # The synced copy is inside the tagged commit (git show strips the
        # trailing newline; content equality is what matters).
        assert _git(skill_home, "show", "v1.1.0:references/prompt-style.md") == "# house style"

        conflict = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [
                    {"path": "SKILL.md", "content": "# v3\n"},
                    {"path": "references/prompt-style.md", "content": "# stale\n"},
                ],
                "new_tag": "v1.2.0",
                "message": "m",
            },
        )
        assert conflict.status_code == 422
        assert conflict.json()["detail"]["errors"][0]["path"] == "references/prompt-style.md"


def test_save_skill_version_path_escape_is_422(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        before = _git(skill_home, "rev-parse", "HEAD")
        response = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": "../evil.md", "content": "x"}],
                "new_tag": "v2.0.0",
                "message": "m",
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["errors"][0]["path"] == "../evil.md"
        assert _git(skill_home, "rev-parse", "HEAD") == before


def test_save_skill_version_tag_conflict_is_409(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        response = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": "SKILL.md", "content": "# V2\n"}],
                "new_tag": "v1.0.0",
                "message": "m",
            },
        )
        assert response.status_code == 409


def test_save_skill_version_contract_failure_rolls_back(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        before = _git(skill_home, "rev-parse", "HEAD")
        response = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": "SKILL.md", "content": ""}],
                "new_tag": "v2.0.0",
                "message": "m",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["errors"]
        assert _git(skill_home, "rev-parse", "HEAD") == before
        assert _git(skill_home, "tag", "--list") == "v1.0.0"
        assert _git(skill_home, "status", "--porcelain") == ""


def test_save_skill_version_git_metadata_path_is_422(client_factory, job_db, skill_home) -> None:
    # Any-case .git at any level is rejected (PR #224 review P0): on
    # case-insensitive filesystems `.GIT/hooks/` lands in the metadata dir.
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        response = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": ".GIT/hooks/pre-commit", "content": "#!/bin/sh\nexit 1\n"}],
                "new_tag": "v2.0.0",
                "message": "m",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["errors"]


def test_save_skill_version_payload_bounds(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        oversized = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": "SKILL.md", "content": "x" * (128 * 1024 + 1)}],
                "new_tag": "v2.0.0",
                "message": "m",
            },
        )
        assert oversized.status_code == 422
        too_many = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": f"f{i}.md", "content": "x"} for i in range(101)],
                "new_tag": "v2.0.0",
                "message": "m",
            },
        )
        assert too_many.status_code == 422


def test_default_detail_follows_head_after_save(client_factory, job_db, skill_home) -> None:
    # #322 latest semantics: after save_skill_version the default detail
    # serves the NEW HEAD's content, the old tag stays readable through
    # ?ref=, and a seeded v1.0.0 lock pin is never touched by the save.
    old_head = _git(skill_home, "rev-parse", "HEAD")
    store = SkillLockStore(job_db.dsn_identity)
    lock = store.get_lock() or SkillsLock()
    lock.skills[_KEY] = LockedSkill(repo=str(skill_home), refs={"v1.0.0": old_head})
    store.put_lock(lock)
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        saved = scoped.post(
            f"{_TOOLS}/{_KEY}/versions",
            json={
                "files": [{"path": "SKILL.md", "content": "# Write Script v2\n"}],
                "new_tag": "v1.1.0",
                "message": "revise",
            },
        )
        assert saved.status_code == 201, saved.text

        default = scoped.get(f"{_TOOLS}/{_KEY}")
        assert default.status_code == 200, default.text
        assert default.json()["ref"] == "latest"
        assert default.json()["commit"] == _git(skill_home, "rev-parse", "HEAD")
        head_md = next(f for f in default.json()["files"] if f["path"] == "SKILL.md")
        assert head_md["content"] == "# Write Script v2\n"

        tagged = scoped.get(f"{_TOOLS}/{_KEY}", params={"ref": "v1.0.0"})
        assert tagged.json()["commit"] == old_head
        tagged_md = next(f for f in tagged.json()["files"] if f["path"] == "SKILL.md")
        assert tagged_md["content"] == "# Write Script\n"

        # The seeded pin is untouched by the save.
        assert (store.get_lock() or SkillsLock()).skills[_KEY].refs == {"v1.0.0": old_head}


def test_foreign_workspace_binding_is_refused(client_factory, job_db, skill_home) -> None:
    """#710: a run token bound to another workspace cannot read/validate/
    version this workspace's skills — the tool surface matches create_skill
    (#633) and the job tools (require_studio_agent_workspace)."""
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
        assert bound.get(f"{_TOOLS}/{_KEY}").status_code == 403
        assert bound.post(f"{_TOOLS}/{_KEY}/validate").status_code == 403
        assert (
            bound.post(
                f"{_TOOLS}/{_KEY}/versions",
                json={
                    "files": [{"path": "SKILL.md", "content": "# hijack\n"}],
                    "new_tag": "v9.9.9",
                    "message": "hijack",
                },
            ).status_code
            == 403
        )
        # The repo is untouched.
        assert _git(skill_home, "rev-parse", "HEAD") == _git(
            skill_home, "rev-parse", "v1.0.0^{commit}"
        )


def test_skill_key_workspace_mismatch_is_refused(client_factory, job_db, skill_home) -> None:
    """A bound token cannot reach a foreign workspace's skill through a key
    whose workspace segment disagrees with the path scope (the key IS
    <workspace>/<name>; a mismatched pair 404s like an unknown skill)."""
    with client_factory(fresh=True) as client:
        other = job_db.create_workspace(
            "Other WS", default_workflow_key="other_ws_flow", workspace_id="other_ws_flow"
        )
        # Token bound to the OTHER workspace, calling the OTHER workspace's
        # path scope but with THIS workspace's skill key: the key's own
        # workspace segment wins — 404, not a cross-workspace read.
        admin_id = str(job_db.get_user_credentials("admin")["id"])
        token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=str(other["id"]))
        scoped = client.__class__(client.app)
        scoped.headers["authorization"] = f"Bearer {token}"
        mismatched = f"/api/studio-agent/tools/workspaces/other_ws_flow/skills/{_KEY}"
        assert scoped.get(mismatched).status_code == 404
        assert scoped.post(f"{mismatched}/validate").status_code == 404


def test_unbound_token_requires_workspace_membership(client_factory, job_db, skill_home) -> None:
    """#710 follow-up: an UNBOUND self-service token (origin 'user', the MCP
    server's external-agent credential) falls back to a membership check —
    the minter must be a member of the addressed workspace. A leaked unbound
    token no longer reads or commit+tags every workspace's skill repos."""
    with client_factory(fresh=True) as client:
        member_id = client.post(
            "/api/users",
            json={"username": "ws_member", "password": "pw-member"},
            headers={"x-agent-legion-request": "1"},
        ).json()["id"]
        job_db.upsert_workspace_member(_WS, member_id, "viewer")

        # Minter IS a member → reads fine (unbound keeps membership-only
        # semantics, not a blanket refusal).
        member_token = scoped_tokens.mint_scoped_token(job_db, member_id)
        member_scoped = client.__class__(client.app)
        member_scoped.headers["authorization"] = f"Bearer {member_token}"
        assert member_scoped.get(f"{_TOOLS}/{_KEY}").status_code == 200

        # Minter is NOT a member of any workspace → 404 everywhere, and the
        # write surface never fires (repo HEAD unchanged).
        outsider_id = client.post(
            "/api/users",
            json={"username": "ws_outsider", "password": "pw-out"},
            headers={"x-agent-legion-request": "1"},
        ).json()["id"]
        outsider_token = scoped_tokens.mint_scoped_token(job_db, outsider_id)
        outsider = client.__class__(client.app)
        outsider.headers["authorization"] = f"Bearer {outsider_token}"
        assert outsider.get(f"{_TOOLS}/{_KEY}").status_code == 404
        assert (
            outsider.post(
                f"{_TOOLS}/{_KEY}/versions",
                json={
                    "files": [{"path": "SKILL.md", "content": "# hijack\n"}],
                    "new_tag": "v9.9.9",
                    "message": "hijack",
                },
            ).status_code
            == 404
        )
        assert _git(skill_home, "rev-parse", "HEAD") == _git(
            skill_home, "rev-parse", "v1.0.0^{commit}"
        )
