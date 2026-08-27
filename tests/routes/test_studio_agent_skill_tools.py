"""Studio-agent skill tool endpoints (/api/studio-agent/tools/skills/*, #217).

Scoped tokens get read/validate/save-version over the LOCAL skill repos;
full user sessions are refused at the scope guard (see
test_studio_agent_tools.py for the 401/403 inventory). save_skill_version
commits + tags the in-place repo but never touches the DB skill lock.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from server.app.auth import scoped_tokens
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.config import SkillsConfig, SkillSourceConfig

_KEY = "education-video-problems-generation/write-script"
_URL_KEY = "education-video-problems-generation/review-script"
_TOOLS = "/api/studio-agent/tools/skills"


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
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    _git(repo, "tag", tag)


@pytest.fixture
def skill_home(tmp_path, monkeypatch):
    base = tmp_path / "home" / ".agents" / "skills" / "agent-legion"
    _make_skill_repo(base / _KEY)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return base / _KEY


def _scoped(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped


def test_get_skill_and_ref_preview(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        locked = scoped.get(f"{_TOOLS}/{_KEY}")
        assert locked.status_code == 200, locked.text
        assert locked.json()["ref"] == "v1.0.0"
        assert locked.json()["available"] is True
        assert locked.json()["tags"] == ["v1.0.0"]
        assert any(f["path"] == "SKILL.md" for f in locked.json()["files"])

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
        tagged = client.get(f"/api/executors/skills/{_KEY}", params={"ref": "v1.0.0"})
        assert tagged.status_code == 200, tagged.text
        assert tagged.json()["ref"] == "v1.0.0"
        assert (
            client.get(f"/api/executors/skills/{_KEY}", params={"ref": "nope"}).status_code == 404
        )


def test_validate_skill(client_factory, job_db, skill_home) -> None:
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        ok = scoped.post(f"{_TOOLS}/{_KEY}/validate")
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"key": _KEY, "valid": True, "errors": []}

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
        assert _git(skill_home, "log", "-1", "--format=%an <%ae>") == (
            "agent-legion-studio <studio@local>"
        )
        # The configured ref and the lock are untouched: no relock happened.
        detail = scoped.get(f"{_TOOLS}/{_KEY}")
        assert detail.json()["ref"] == "v1.0.0"
        preview = scoped.get(f"{_TOOLS}/{_KEY}", params={"ref": "v1.1.0"})
        skill_md = next(f for f in preview.json()["files"] if f["path"] == "SKILL.md")
        assert skill_md["content"] == "# Write Script v2\n"


def test_save_skill_version_rejects_url_source(client_factory, job_db, skill_home) -> None:
    store = SkillSourceStore(job_db.path)
    sources = store.get_sources() or SkillsConfig()
    sources.skills[_URL_KEY] = SkillSourceConfig(repo="https://example.com/skill.git", ref="v1")
    store.put_sources(sources)
    with client_factory(fresh=True) as client:
        scoped = _scoped(client, job_db)
        response = scoped.post(
            f"{_TOOLS}/{_URL_KEY}/versions",
            json={
                "files": [{"path": "SKILL.md", "content": "x"}],
                "new_tag": "v2.0.0",
                "message": "m",
            },
        )
        assert response.status_code == 400
        assert "local path" in response.json()["detail"]


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
