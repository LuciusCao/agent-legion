import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.services.skill_source_store import SkillSourceStore
from server.app.skills.config import SkillsConfig, SkillsLock
from tests.postgres_support import TEST_DATABASE_URL


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


def _make_git_repo(repo: Path, tag: str = "v1.0.0") -> str:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "SKILL.md").write_text("# Review\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    _git(repo, "tag", tag)
    return _git(repo, "rev-parse", "HEAD")


def _put_document(sources: dict, lock: dict | None = None) -> None:
    store = SkillSourceStore(TEST_DATABASE_URL)
    store.put_sources(SkillsConfig.model_validate({"skills": sources}))
    store.put_lock(SkillsLock.model_validate(lock or {}))


def test_skill_detail_lists_safe_text_files_and_locked_version(tmp_path: Path) -> None:
    _put_document(
        {"demo/review": {"repo": "local", "ref": "v1.2.0"}},
        {"skills": {"demo/review": {"repo": "local", "ref": "v1.2.0", "commit": "abc123"}}},
    )
    base = tmp_path / "skills"
    skill = base / "demo" / "review"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text("# Review\n")
    (skill / "references" / "rules.md").write_text("rules\n")
    (skill / "scripts" / "validate.py").write_text("print('ok')\n")
    (skill / "scripts" / "ignored.bin").write_bytes(b"binary")

    detail = SkillCatalogService(TEST_DATABASE_URL, base).detail("demo/review")

    assert detail["ref"] == "v1.2.0"
    assert detail["commit"] == "abc123"
    assert detail["tags"] == []  # not a git repo: no tags to list
    assert [item["path"] for item in detail["files"]] == [
        "SKILL.md",
        "references/rules.md",
        "scripts/validate.py",
    ]


def test_skill_detail_rejects_unconfigured_keys(tmp_path: Path) -> None:
    _put_document({})

    with pytest.raises(NotFoundError):
        SkillCatalogService(TEST_DATABASE_URL).detail("../secret")


def test_locked_detail_reads_locked_commit_from_local_source_repo(tmp_path: Path) -> None:
    """Non-in-place local source (PR #224 review): the declared repo lives
    outside the managed base dir, so ref/tags/locked reads must resolve to it,
    not to the (absent) cache dir. The default detail serves the LOCKED
    commit's content even when the working tree moved on."""
    repo_dir = tmp_path / "src" / "demo" / "review"
    commit = _make_git_repo(repo_dir)
    _put_document(
        {"demo/review": {"repo": str(repo_dir), "ref": "v1.0.0"}},
        {"skills": {"demo/review": {"repo": str(repo_dir), "ref": "v1.0.0", "commit": commit}}},
    )
    (repo_dir / "SKILL.md").write_text("# DIRTY working tree\n", encoding="utf-8")

    service = SkillCatalogService(TEST_DATABASE_URL, tmp_path / "cache")
    detail = service.detail("demo/review")

    assert detail["commit"] == commit
    assert detail["available"] is True
    assert detail["tags"] == ["v1.0.0"]
    skill_md = next(f for f in detail["files"] if f["path"] == "SKILL.md")
    assert skill_md["content"] == "# Review\n"

    preview = service.detail("demo/review", ref="v1.0.0")
    assert preview["commit"] == commit
    assert preview["tags"] == ["v1.0.0"]


def test_default_detail_falls_back_to_working_tree_without_lock(tmp_path: Path) -> None:
    """Seed scenario (no lock entry): the working tree is the content source."""
    repo_dir = tmp_path / "src" / "demo" / "review"
    _make_git_repo(repo_dir)
    _put_document({"demo/review": {"repo": str(repo_dir), "ref": "v1.0.0"}})

    detail = SkillCatalogService(TEST_DATABASE_URL, tmp_path / "cache").detail("demo/review")

    assert detail["commit"] == ""
    assert detail["available"] is True
    assert detail["tags"] == ["v1.0.0"]
    skill_md = next(f for f in detail["files"] if f["path"] == "SKILL.md")
    assert skill_md["content"] == "# Review\n"
