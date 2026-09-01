import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.services.skill_lock_store import SkillLockStore
from server.app.skills.config import SkillsLock
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


def _put_lock(lock: dict) -> None:
    store = SkillLockStore(TEST_DATABASE_URL)
    store.put_lock(SkillsLock.model_validate(lock))


def test_skill_detail_lists_safe_text_files_and_follows_head(tmp_path: Path) -> None:
    """#322: the default detail is the in-place repo at HEAD (``latest``)."""
    base = tmp_path / "skills"
    skill = base / "demo" / "review"
    commit = _make_git_repo(skill)
    (skill / "references").mkdir()
    (skill / "scripts").mkdir()
    (skill / "references" / "rules.md").write_text("rules\n")
    (skill / "scripts" / "validate.py").write_text("print('ok')\n")
    (skill / "scripts" / "ignored.bin").write_bytes(b"binary")

    detail = SkillCatalogService(TEST_DATABASE_URL, base).detail("demo/review")

    assert detail["ref"] == "latest"
    assert detail["commit"] == commit
    assert detail["available"] is True
    assert detail["tags"] == ["v1.0.0"]
    assert [item["path"] for item in detail["files"]] == [
        "SKILL.md",
        "references/rules.md",
        "scripts/validate.py",
    ]


def test_skill_detail_rejects_invalid_keys(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        SkillCatalogService(TEST_DATABASE_URL, tmp_path / "skills").detail("../secret")


def test_default_detail_reads_head_while_ref_preview_reads_the_tag(tmp_path: Path) -> None:
    """latest semantics: after a new commit the default detail serves the new
    HEAD content; the tag preview keeps the old commit addressable."""
    base = tmp_path / "skills"
    repo = base / "demo" / "review"
    old_commit = _make_git_repo(repo)
    (repo / "SKILL.md").write_text("# Review v2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "v2", "--no-gpg-sign")
    head = _git(repo, "rev-parse", "HEAD")

    service = SkillCatalogService(TEST_DATABASE_URL, base)
    detail = service.detail("demo/review")

    assert detail["ref"] == "latest"
    assert detail["commit"] == head
    skill_md = next(f for f in detail["files"] if f["path"] == "SKILL.md")
    assert skill_md["content"] == "# Review v2\n"

    preview = service.detail("demo/review", ref="v1.0.0")
    assert preview["commit"] == old_commit
    assert preview["tags"] == ["v1.0.0"]
    preview_md = next(f for f in preview["files"] if f["path"] == "SKILL.md")
    assert preview_md["content"] == "# Review\n"


def test_default_detail_marks_missing_repo_unavailable(tmp_path: Path) -> None:
    detail = SkillCatalogService(TEST_DATABASE_URL, tmp_path / "skills").detail("demo/review")

    assert detail["ref"] == "latest"
    assert detail["commit"] == ""
    assert detail["available"] is False
    assert detail["tags"] == []
    assert detail["files"] == []


def test_metadata_reports_a_sole_pin(tmp_path: Path) -> None:
    _put_lock({"skills": {"demo/review": {"repo": "r", "refs": {"v1.2.0": "a" * 40}}}})

    metadata = SkillCatalogService(TEST_DATABASE_URL, tmp_path / "skills").metadata("demo/review")

    assert metadata == {"skill_ref": "v1.2.0", "skill_commit": "a" * 40}


def test_metadata_is_empty_without_a_lock_entry_or_with_ambiguous_pins(tmp_path: Path) -> None:
    service = SkillCatalogService(TEST_DATABASE_URL, tmp_path / "skills")
    assert service.metadata("demo/review") == {}

    # #322: no declared default ref — multiple pins have no unambiguous answer.
    _put_lock({"skills": {"demo/review": {"repo": "r", "refs": {"v1": "a" * 40, "v2": "b" * 40}}}})
    assert service.metadata("demo/review") == {}
