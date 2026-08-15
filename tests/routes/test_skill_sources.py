from __future__ import annotations

import os
import subprocess
from pathlib import Path

from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES

CSRF = {"x-agent-legion-request": "1"}
SKILL_SOURCES_URL = "/api/admin/skill-sources"
RELOCK_URL = "/api/admin/skill-sources/relock"
KEY = "question_comprehension_info/generate_key_info"


def _member_client(client, username="skill_source_member", password="pw1"):
    response = client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _git_env() -> dict[str, str]:
    env = {**dict(os.environ)}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    # Never leak a parent repo's hook environment into the temp repos.
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    ).stdout.strip()


def _make_repo(repo: Path, ref: str) -> str:
    """In-place local skill repo with one commit tagged ``ref``; returns the commit."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "SKILL.md").write_text(f"# {repo.name}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--no-gpg-sign")
    _git(repo, "tag", ref)
    return _git(repo, "rev-parse", "HEAD")


def _seed_local_repos(fake_home: Path) -> None:
    """Materialize every built-in source as a real local repo under the fake HOME."""
    base = fake_home / ".agents" / "skills" / "agent-legion"
    for key, source in BUILTIN_SKILL_SOURCES.skills.items():
        assert source.repo.startswith("~/.agents/skills/agent-legion/")
        relative = source.repo.removeprefix("~/.agents/skills/agent-legion/")
        assert relative == key
        _make_repo(base / relative, source.ref)


def test_get_requires_auth(anon_client) -> None:
    assert anon_client.get(SKILL_SOURCES_URL).status_code == 401


def test_put_requires_auth(anon_client) -> None:
    response = anon_client.put(f"{SKILL_SOURCES_URL}/{KEY}", json={"repo": "r", "ref": "v"})
    assert response.status_code == 401


def test_relock_requires_auth(anon_client) -> None:
    assert anon_client.post(RELOCK_URL).status_code == 401


def test_member_forbidden(client) -> None:
    member = _member_client(client)
    assert member.get(SKILL_SOURCES_URL).status_code == 403
    assert (
        member.put(f"{SKILL_SOURCES_URL}/{KEY}", json={"repo": "r", "ref": "v"}).status_code == 403
    )
    assert member.post(RELOCK_URL).status_code == 403


def test_get_merged_view_after_seed(client) -> None:
    response = client.get(SKILL_SOURCES_URL)
    assert response.status_code == 200, response.text
    skills = response.json()["skills"]
    assert len(skills) == len(BUILTIN_SKILL_SOURCES.skills) == 9
    locked = BUILTIN_SKILL_LOCK.skills[KEY]
    resolved_at = BUILTIN_SKILL_LOCK.resolved_at
    entry = next(item for item in skills if item["key"] == KEY)
    assert entry == {
        "key": KEY,
        "repo": locked.repo,
        "ref": locked.ref,
        "locked_commit": locked.commit,
        "resolved_at": resolved_at,
        "stale": False,
    }
    assert all(not item["stale"] for item in skills)


def test_put_creates_unknown_key(client) -> None:
    """Unknown keys are created, not 404'd: declaring a brand-new skill
    source over the API is the fresh-deployment bootstrap path."""
    response = client.put(
        f"{SKILL_SOURCES_URL}/brand_new/skill",
        json={"repo": "https://example.com/new.git", "ref": "v1.0.0"},
    )
    assert response.status_code == 200, response.text
    skills = response.json()["skills"]
    assert len(skills) == len(BUILTIN_SKILL_SOURCES.skills) + 1
    created = next(item for item in skills if item["key"] == "brand_new/skill")
    assert created["repo"] == "https://example.com/new.git"
    assert created["ref"] == "v1.0.0"
    # The lock has no entry for the new key, so it is stale until relock.
    assert created["locked_commit"] is None
    assert created["stale"] is True

    persisted = client.get(SKILL_SOURCES_URL).json()["skills"]
    assert next(item for item in persisted if item["key"] == "brand_new/skill")["ref"] == "v1.0.0"


def test_put_creates_document_when_never_seeded(client) -> None:
    """Fresh deployment with no skill_sources document at all: the first PUT
    creates the document with just the declared entry."""
    from server.app.db.transaction import write_transaction
    from tests.postgres_support import TEST_DATABASE_URL

    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from global_settings where key='skill_sources'")

    response = client.put(
        f"{SKILL_SOURCES_URL}/wf/cap",
        json={"repo": "https://example.com/s.git", "ref": "main"},
    )
    assert response.status_code == 200, response.text
    skills = response.json()["skills"]
    assert [item["key"] for item in skills] == ["wf/cap"]
    assert skills[0]["repo"] == "https://example.com/s.git"
    assert skills[0]["ref"] == "main"
    assert skills[0]["stale"] is True


def test_put_updates_source_and_marks_stale(client) -> None:
    source = BUILTIN_SKILL_SOURCES.skills[KEY]
    response = client.put(
        f"{SKILL_SOURCES_URL}/{KEY}",
        json={"repo": source.repo, "ref": "v9.9.9"},
    )
    assert response.status_code == 200, response.text
    skills = response.json()["skills"]
    changed = next(item for item in skills if item["key"] == KEY)
    assert changed["ref"] == "v9.9.9"
    # The lock still pins the old ref, so the entry is stale until relock.
    assert changed["locked_commit"] == BUILTIN_SKILL_LOCK.skills[KEY].commit
    assert changed["stale"] is True
    assert all(item["stale"] is (item["key"] == KEY) for item in skills)

    persisted = client.get(SKILL_SOURCES_URL).json()["skills"]
    assert next(item for item in persisted if item["key"] == KEY)["stale"] is True


def test_relock_resolves_local_repos(client, tmp_path, monkeypatch) -> None:
    """End-to-end relock: every built-in source is a real local repo; after
    retagging one ref, POST relock rewrites the lock to the new commits."""
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    _seed_local_repos(fake_home)

    repo = fake_home / ".agents" / "skills" / "agent-legion" / KEY
    (repo / "SKILL.md").write_text("# updated\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "update", "--no-gpg-sign")
    _git(repo, "tag", "v9.9.9")
    new_commit = _git(repo, "rev-parse", "v9.9.9^{}")

    source = BUILTIN_SKILL_SOURCES.skills[KEY]
    response = client.put(
        f"{SKILL_SOURCES_URL}/{KEY}",
        json={"repo": source.repo, "ref": "v9.9.9"},
    )
    assert response.status_code == 200, response.text
    assert next(item for item in response.json()["skills"] if item["key"] == KEY)["stale"] is True

    response = client.post(RELOCK_URL)
    assert response.status_code == 200, response.text
    skills = response.json()["skills"]
    assert len(skills) == 9
    assert all(not item["stale"] for item in skills)
    entry = next(item for item in skills if item["key"] == KEY)
    assert entry["ref"] == "v9.9.9"
    assert entry["locked_commit"] == new_commit
    assert entry["resolved_at"]
