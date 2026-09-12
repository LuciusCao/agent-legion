"""Studio-agent skill creation tool endpoint (#633, workspace-scoped).

POST /api/studio-agent/tools/workspaces/{id}/skills materializes a fresh
git skill repo under the workspace's skill dir (~/.agents/skills/<id>/):
the contract trio must ride the request, the create is draft-only (never
touches the DB skill lock, never publishes), and a failed create leaves no
half-initialized repo behind. Scope behavior (401/403 inventory, workspace
binding) is covered by test_studio_agent_tools.py / test_studio_agent_scope.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from server.app.auth import scoped_tokens

_TOOLS = "/api/studio-agent/tools/workspaces"
_WS = "ws-create-skills"
_CREATE_URL = f"{_TOOLS}/{_WS}/skills"

_TRIO = [
    {"path": "SKILL.md", "content": "# New Skill\n\nDoes a thing.\n"},
    {"path": "references/output-contract.md", "content": "# contract\n"},
    {"path": "scripts/validate_output.py", "content": "raise SystemExit(0)\n"},
]


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


@pytest.fixture
def skill_home(tmp_path, monkeypatch, job_db):
    """HOME patched to a temp root; the workspace exists in the job DB."""
    base = tmp_path / "home" / ".agents" / "skills"
    base.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = job_db.create_workspace(_WS, default_workflow_key="ws_create_flow")
    assert workspace is not None
    return base


def _scoped(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped


def test_create_skill_materializes_repo_with_trio_commit_and_tag(
    client_factory, job_db, skill_home
) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        created = scoped.post(
            _CREATE_URL,
            json={
                "skill_name": "generate-quiz",
                "files": _TRIO,
                "new_tag": "v0.1.0",
                "message": "initial skill",
            },
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["key"] == f"{_WS}/generate-quiz"
        assert payload["tag"] == "v0.1.0"
        assert payload["commit"]

        repo = skill_home / _WS / "generate-quiz"
        assert repo.is_dir()
        assert payload["commit"] == _git(repo, "rev-parse", "HEAD")
        assert _git(repo, "tag", "--list") == "v0.1.0"
        assert _git(repo, "log", "-1", "--format=%an <%ae>") == (
            "agent-legion-studio <studio@local>"
        )
        assert _git(repo, "status", "--porcelain") == ""
        for path in ("SKILL.md", "references/output-contract.md", "scripts/validate_output.py"):
            assert (repo / path).is_file()

        # The created skill immediately reads through the existing tools.
        detail = scoped.get(f"/api/studio-agent/tools/skills/{_WS}/generate-quiz")
        assert detail.status_code == 200, detail.text
        assert detail.json()["tags"] == ["v0.1.0"]


def test_create_skill_conflict_when_directory_exists(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        first = scoped.post(
            _CREATE_URL,
            json={
                "skill_name": "taken",
                "files": _TRIO,
                "new_tag": "v0.1.0",
                "message": "m",
            },
        )
        assert first.status_code == 201, first.text
        second = scoped.post(
            _CREATE_URL,
            json={
                "skill_name": "taken",
                "files": _TRIO,
                "new_tag": "v0.2.0",
                "message": "m",
            },
        )
        assert second.status_code == 409

        # A plain FILE at the target path is also a conflict.
        (skill_home / _WS / "blocker").write_text("not a dir", encoding="utf-8")
        blocked = scoped.post(
            _CREATE_URL,
            json={"skill_name": "blocker", "files": _TRIO, "new_tag": "v1", "message": "m"},
        )
        assert blocked.status_code == 409
        assert (skill_home / _WS / "blocker").read_text(encoding="utf-8") == "not a dir"


def test_create_skill_404_for_unknown_workspace(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        response = scoped.post(
            f"{_TOOLS}/ws-missing/skills",
            json={
                "skill_name": "orphan",
                "files": _TRIO,
                "new_tag": "v0.1.0",
                "message": "m",
            },
        )
        assert response.status_code == 404
        # Nothing was created anywhere near the (unknown) workspace.
        assert not (skill_home / "ws-missing").exists()


def test_create_skill_rejections_are_422(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)

        def post(**overrides):
            payload = {
                "skill_name": "new-skill",
                "files": _TRIO,
                "new_tag": "v0.1.0",
                "message": "m",
            }
            payload.update(overrides)
            return scoped.post(_CREATE_URL, json=payload)

        # Invalid skill name (uppercase / slash / too long).
        assert post(skill_name="Bad Name").status_code == 422
        assert post(skill_name="a/b").status_code == 422
        assert post(skill_name="x" * 65).status_code == 422
        # Missing contract trio member.
        assert post(files=_TRIO[:2]).status_code == 422
        # Empty SKILL.md fails the contract too.
        assert (
            post(
                files=[
                    {"path": "SKILL.md", "content": "   "},
                    _TRIO[1],
                    _TRIO[2],
                ]
            ).status_code
            == 422
        )
        # Path escape and git metadata paths.
        assert post(files=_TRIO + [{"path": "../evil.md", "content": "x"}]).status_code == 422
        assert (
            post(files=_TRIO + [{"path": ".GIT/hooks/pre-commit", "content": "x"}]).status_code
            == 422
        )
        # Invalid tag names.
        assert post(new_tag="-start").status_code == 422
        assert post(new_tag="bad..tag").status_code == 422
        # Bounds: too many files, oversized content.
        assert (
            post(files=[{"path": f"f{i}.md", "content": "x"} for i in range(101)]).status_code
            == 422
        )
        assert (
            post(files=[{"path": "SKILL.md", "content": "x" * (128 * 1024 + 1)}]).status_code == 422
        )

        # None of the rejections left a directory behind.
        assert not (skill_home / _WS / "new-skill").exists()
        assert list((skill_home / _WS).iterdir()) == []


def test_create_skill_missing_trio_reports_structured_errors(
    client_factory, job_db, skill_home
) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        response = scoped.post(
            _CREATE_URL,
            json={
                "skill_name": "incomplete",
                "files": [{"path": "SKILL.md", "content": "# Only skill md\n"}],
                "new_tag": "v0.1.0",
                "message": "m",
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert {
            "path": "references/output-contract.md",
            "error": ("missing references/output-contract.md"),
        } in detail["errors"]
        assert {
            "path": "scripts/validate_output.py",
            "error": ("missing scripts/validate_output.py"),
        } in detail["errors"]
        assert not (skill_home / _WS / "incomplete").exists()
