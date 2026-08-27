"""SkillEditingService: contract validation and all-or-nothing version authoring."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.skill_editing import (
    SkillEditingService,
    SkillEditValidationError,
    SkillFileWrite,
)
from server.app.services.skill_repo import detail_at_ref
from server.app.services.skill_source_store import InMemorySkillSourceStore
from server.app.skills.config import SkillsConfig, SkillsLock, SkillSourceConfig

pytestmark = pytest.mark.no_db

_KEY = "wf/review"


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


def _make_repo(repo: Path, tag: str = "v1.0.0") -> str:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "SKILL.md").write_text("# Review\n", encoding="utf-8")
    (repo / "references").mkdir()
    (repo / "references" / "output-contract.md").write_text("# contract\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "validate_output.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init", "--no-gpg-sign")
    _git(repo, "tag", tag)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _make_skill_repo(tmp_path)


def _make_skill_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "skills" / "wf" / "review"
    _make_repo(repo_dir)
    return repo_dir


@pytest.fixture
def store(repo: Path) -> InMemorySkillSourceStore:
    return InMemorySkillSourceStore(
        sources=SkillsConfig(skills={_KEY: SkillSourceConfig(repo=str(repo), ref="v1.0.0")}),
        lock=SkillsLock(),
    )


@pytest.fixture
def service(store: InMemorySkillSourceStore, repo: Path) -> SkillEditingService:
    return SkillEditingService(store, base_dir=repo.parents[1])


def test_validate_happy_path(service: SkillEditingService) -> None:
    result = service.validate(_KEY)
    assert result == {"key": _KEY, "valid": True, "errors": []}


def test_validate_reports_every_missing_contract_file(
    service: SkillEditingService, repo: Path
) -> None:
    (repo / "references" / "output-contract.md").unlink()
    (repo / "scripts" / "validate_output.py").unlink()
    result = service.validate(_KEY)
    assert result["valid"] is False
    assert {e["path"] for e in result["errors"]} == {
        "references/output-contract.md",
        "scripts/validate_output.py",
    }


def test_validate_unknown_skill_404(service: SkillEditingService) -> None:
    with pytest.raises(NotFoundError):
        service.validate("wf/missing")


def test_save_version_commits_and_tags(service: SkillEditingService, repo: Path) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    result = service.save_version(
        _KEY,
        [SkillFileWrite(path="SKILL.md", content="# Review v2\n")],
        "v1.1.0",
        "revise review skill",
    )
    assert result["tag"] == "v1.1.0"
    assert result["files"] == ["SKILL.md"]
    assert result["commit"] == _git(repo, "rev-parse", "HEAD")
    assert result["commit"] != before
    assert _git(repo, "rev-parse", "v1.1.0^{commit}") == result["commit"]
    assert (repo / "SKILL.md").read_text(encoding="utf-8") == "# Review v2\n"
    author = _git(repo, "log", "-1", "--format=%an <%ae>")
    assert author == "agent-legion-studio <studio@local>"
    # The tag keeps the old content addressable without any relock.
    preview = detail_at_ref(_KEY, "v1.0.0", repo)
    skill_md = next(f for f in preview["files"] if f["path"] == "SKILL.md")
    assert skill_md["content"] == "# Review\n"
    assert preview["tags"] == ["v1.1.0", "v1.0.0"]


def test_detail_at_ref_lists_tags_latest_version_first(repo: Path) -> None:
    _git(repo, "tag", "v1.2.0")
    _git(repo, "tag", "v1.10.0")
    _git(repo, "tag", "v0.9")
    preview = detail_at_ref(_KEY, "v1.0.0", repo)
    assert preview["tags"] == ["v1.10.0", "v1.2.0", "v1.0.0", "v0.9"]


def test_save_version_never_touches_the_lock(
    service: SkillEditingService, store: InMemorySkillSourceStore
) -> None:
    service.save_version(_KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v9.9.9", "m")
    assert store.get_lock() == SkillsLock()
    assert store.get_sources().skills[_KEY].ref == "v1.0.0"


def test_save_version_rejects_url_source(service: SkillEditingService) -> None:
    store = InMemorySkillSourceStore(
        sources=SkillsConfig(
            skills={"wf/remote": SkillSourceConfig(repo="https://example.com/x.git", ref="v1")}
        )
    )
    service = SkillEditingService(store)
    with pytest.raises(InvalidOperationError, match="local path"):
        service.save_version("wf/remote", [SkillFileWrite(path="SKILL.md", content="x")], "v2", "m")


def test_save_version_tag_conflict(service: SkillEditingService) -> None:
    with pytest.raises(ConflictError, match="v1.0.0"):
        service.save_version(
            _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v1.0.0", "m"
        )


def test_save_version_rejects_invalid_tag_name(service: SkillEditingService) -> None:
    with pytest.raises(SkillEditValidationError, match="Invalid tag"):
        service.save_version(
            _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "bad tag!", "m"
        )


def test_save_version_rejects_dirty_tree(service: SkillEditingService, repo: Path) -> None:
    (repo / "SKILL.md").write_text("# dirty\n", encoding="utf-8")
    with pytest.raises(ConflictError, match="uncommitted"):
        service.save_version(
            _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v2.0.0", "m"
        )


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.md", "/abs/file.md", ".git/config", "sub/../../escape.md", "a/.gitx/../../b.md"],
)
def test_save_version_rejects_escaping_paths(
    service: SkillEditingService, repo: Path, bad_path: str
) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(SkillEditValidationError) as excinfo:
        service.save_version(_KEY, [SkillFileWrite(path=bad_path, content="x")], "v2.0.0", "m")
    assert excinfo.value.errors
    # Nothing was written or committed.
    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "status", "--porcelain") == ""


def test_save_version_rejects_overwriting_untracked_file(
    service: SkillEditingService, repo: Path
) -> None:
    # An ignored file keeps `git status` clean, so the overwrite guard (not the
    # dirty-tree guard) is what fires; rollback could never restore its content.
    (repo / ".gitignore").write_text("notes.txt\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore notes", "--no-gpg-sign")
    (repo / "notes.txt").write_text("precious\n", encoding="utf-8")
    with pytest.raises(SkillEditValidationError, match="Unsafe"):
        service.save_version(
            _KEY, [SkillFileWrite(path="notes.txt", content="overwrite")], "v2.0.0", "m"
        )
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "precious\n"


def test_save_version_rolls_back_when_contract_fails(
    service: SkillEditingService, repo: Path
) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(SkillEditValidationError) as excinfo:
        service.save_version(
            _KEY,
            [
                SkillFileWrite(path="SKILL.md", content=""),  # empty -> contract failure
                SkillFileWrite(path="references/new.md", content="# new\n"),
            ],
            "v2.0.0",
            "m",
        )
    assert any(e["path"] == "SKILL.md" for e in excinfo.value.errors)
    # Rolled back exactly: original HEAD, original content, new file removed, no tag.
    assert _git(repo, "rev-parse", "HEAD") == before
    assert (repo / "SKILL.md").read_text(encoding="utf-8") == "# Review\n"
    assert not (repo / "references" / "new.md").exists()
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "tag", "--list") == "v1.0.0"


def test_detail_at_ref_unknown_tag_404(repo: Path) -> None:
    with pytest.raises(NotFoundError, match="v9.9.9"):
        detail_at_ref(_KEY, "v9.9.9", repo)
