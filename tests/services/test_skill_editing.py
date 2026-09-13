"""SkillEditingService: contract validation and all-or-nothing version authoring."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from server.app.services.job_errors import (
    ConflictError,
    NotFoundError,
)
from server.app.services.skill_detail import detail_at_ref
from server.app.services.skill_editing import (
    SkillEditingService,
    SkillEditValidationError,
    SkillFileWrite,
)
from server.app.services.skill_repo import SkillGitError, run_git
from server.app.services.skill_repo_edit import SkillRollbackError, edit_lock_for

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
def service(repo: Path, tmp_path: Path) -> SkillEditingService:
    return SkillEditingService(base_dir=repo.parents[1], runs_dir=tmp_path / "runs")


def test_validate_happy_path(service: SkillEditingService) -> None:
    result = service.validate(_KEY)
    assert result == {
        "key": _KEY,
        "valid": True,
        "errors": [],
        "warnings": [
            {
                "path": "contract.yaml",
                "error": (
                    "contract.yaml is missing; runtime output validation degrades to "
                    "existence-only mode (add a root contract.yaml)"
                ),
            }
        ],
    }


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


def test_validate_unknown_skill_reports_missing_directory(service: SkillEditingService) -> None:
    """#322: no registry to 404 against — an absent in-place directory is a
    structured contract error, not a 404."""
    result = service.validate("wf/missing")
    assert result["valid"] is False
    assert result["errors"] == [{"path": ".", "error": "skill directory does not exist"}]


# --- #542: graded contract.yaml validation (error vs warning) ---


def test_validate_reports_embedded_block_deprecation_warning(
    service: SkillEditingService, repo: Path
) -> None:
    (repo / "references" / "output-contract.md").write_text(
        "```yaml contract\nfiles:\n  - path: a.md\n    format: text\n```\n",
        encoding="utf-8",
    )
    result = service.validate(_KEY)
    assert result["valid"] is True
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert "deprecated" in result["warnings"][0]["error"]
    assert "contract.yaml" in result["warnings"][0]["error"]


def test_validate_reports_clean_when_root_contract_yaml_present(
    service: SkillEditingService, repo: Path
) -> None:
    (repo / "contract.yaml").write_text(
        "files:\n  - path: a.md\n    format: text\n", encoding="utf-8"
    )
    result = service.validate(_KEY)
    assert result == {"key": _KEY, "valid": True, "errors": [], "warnings": []}


@pytest.mark.parametrize(
    "bad_yaml",
    [
        "files: [",  # broken YAML
        "files: []",  # empty files list
        "extra_key: 1\nfiles:\n  - path: a.md\n    format: text\n",  # unknown top key
        "files:\n  - path: /abs.md\n    format: text\n",  # absolute path
        "files:\n  - path: ../escape.md\n    format: text\n",  # escaping path
        "files:\n  - path: a.md\n    format: yaml\n",  # illegal format
        "files:\n  - path: a.json\n    format: json\n",  # json without schema
        "files:\n  - path: a.md\n    format: text\n    schema: {type: object}\n",  # text + schema
        "files:\n  - path: a.json\n    format: json\n    min_chars: 5\n    schema: {type: object}\n",
        "files:\n  - path: a.json\n    format: json\n    schema: {type: nope}\n",  # uncompilable
    ],
)
def test_validate_reports_malformed_contract_yaml_as_error(
    service: SkillEditingService, repo: Path, bad_yaml: str
) -> None:
    (repo / "contract.yaml").write_text(bad_yaml, encoding="utf-8")
    result = service.validate(_KEY)
    assert result["valid"] is False
    assert result["errors"], bad_yaml
    assert all(e["path"].startswith("contract.yaml") for e in result["errors"])


def test_save_version_succeeds_with_missing_contract_warning(
    service: SkillEditingService, repo: Path
) -> None:
    """Migration leniency: no contract.yaml → the save still lands, with the
    warning carried on the response."""
    result = service.save_version(
        _KEY, [SkillFileWrite(path="SKILL.md", content="# Review v2\n")], "v1.1.0", "m"
    )
    assert result["tag"] == "v1.1.0"
    assert result["warnings"], "missing contract must warn on the save response"
    assert "contract.yaml" in result["warnings"][0]["error"]


def test_save_version_rolls_back_on_malformed_contract_yaml(
    service: SkillEditingService, repo: Path
) -> None:
    """Format layer: a broken root contract.yaml is an error like any
    contract failure — all-or-nothing rollback applies."""
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(SkillEditValidationError) as excinfo:
        service.save_version(
            _KEY,
            [
                SkillFileWrite(path="SKILL.md", content="# v2\n"),
                SkillFileWrite(path="contract.yaml", content="files: [\n"),
            ],
            "v2.0.0",
            "m",
        )
    assert any(e["path"] == "contract.yaml" for e in excinfo.value.errors)
    assert _git(repo, "rev-parse", "HEAD") == before
    assert (repo / "SKILL.md").read_text(encoding="utf-8") == "# Review\n"
    assert not (repo / "contract.yaml").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_save_version_with_valid_contract_yaml_has_no_warnings(
    service: SkillEditingService, repo: Path
) -> None:
    result = service.save_version(
        _KEY,
        [
            SkillFileWrite(
                path="contract.yaml", content="files:\n  - path: a.md\n    format: text\n"
            )
        ],
        "v1.1.0",
        "m",
    )
    assert result["tag"] == "v1.1.0"
    assert result["warnings"] == []


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


def test_save_version_missing_repo_is_404(service: SkillEditingService) -> None:
    """#322: no registry — the in-place repo itself must exist to save into."""
    with pytest.raises(NotFoundError, match="in-place git repository"):
        service.save_version(
            "wf/missing", [SkillFileWrite(path="SKILL.md", content="x")], "v2.0.0", "m"
        )


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
    [
        "../escape.md",
        "/abs/file.md",
        ".git/config",
        "sub/../../escape.md",
        "a/.gitx/../../b.md",
        # Case-insensitive filesystems (macOS): any-case .git at any level must
        # be rejected, or a hook lands in the metadata dir (PR #224 review P0).
        ".GIT/hooks/pre-commit",
        "sub/.Git/hooks/post-checkout",
        ".Git/config",
    ],
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


def test_save_version_never_runs_repo_hooks(service: SkillEditingService, repo: Path) -> None:
    # --no-verify: an automated authoring flow must not execute hook code.
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    result = service.save_version(
        _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v2.0.0", "m"
    )
    assert result["tag"] == "v2.0.0"


def test_save_version_rejects_dash_leading_tag(service: SkillEditingService) -> None:
    # `git check-ref-format refs/tags/-l` passes but `git tag -l` would list,
    # not create — the 201 would lie about the tag existing (PR #224 review).
    for bad_tag in ("-l", "-v2"):
        with pytest.raises(SkillEditValidationError, match="Invalid tag"):
            service.save_version(
                _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], bad_tag, "m"
            )


def test_save_version_rolls_back_when_tag_step_fails(
    service: SkillEditingService, repo: Path, monkeypatch
) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    real_git = SkillEditingService._git

    def fake_git(repo_dir, args, *, check=True):
        if args and args[0] == "tag":
            raise SkillGitError("simulated tag race")
        return real_git(repo_dir, args, check=check)

    monkeypatch.setattr(SkillEditingService, "_git", staticmethod(fake_git))
    with pytest.raises(SkillGitError, match="simulated tag race"):
        service.save_version(
            _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v2.0.0", "m"
        )
    # Commit happened, tag failed: rollback still restores the original HEAD.
    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "tag", "--list") == "v1.0.0"


def test_rollback_failure_raises_explicit_error(
    service: SkillEditingService, repo: Path, monkeypatch
) -> None:
    real_git = SkillEditingService._git

    def fake_git(repo_dir, args, *, check=True):
        if args and args[0] == "tag":
            raise SkillGitError("simulated tag race")
        if args[:2] == ["reset", "--hard"]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="boom")
        return real_git(repo_dir, args, check=check)

    monkeypatch.setattr(SkillEditingService, "_git", staticmethod(fake_git))
    with pytest.raises(SkillRollbackError, match="manual intervention"):
        service.save_version(
            _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v2.0.0", "m"
        )


def test_error_messages_do_not_leak_host_paths(service: SkillEditingService, repo: Path) -> None:
    (repo / "SKILL.md").write_text("# dirty\n", encoding="utf-8")
    with pytest.raises(ConflictError) as excinfo:
        service.save_version(
            _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v2.0.0", "m"
        )
    assert str(repo) not in str(excinfo.value)
    assert _KEY in str(excinfo.value)


def test_run_git_operational_failure_is_not_a_404(repo: Path) -> None:
    with pytest.raises(SkillGitError):
        run_git(repo, ["show", "deadbeefdeadbeef:SKILL.md"])


def test_save_version_serializes_on_the_repo_lock(
    service: SkillEditingService, repo: Path, tmp_path: Path
) -> None:
    import threading

    lock = edit_lock_for(repo, service.base_dir, tmp_path / "runs")
    completed: list[dict] = []

    def run_save() -> None:
        completed.append(
            service.save_version(
                _KEY, [SkillFileWrite(path="SKILL.md", content="# V2\n")], "v2.0.0", "m"
            )
        )

    with lock:
        worker = threading.Thread(target=run_save)
        worker.start()
        worker.join(timeout=2)
        assert completed == []  # blocked on the held lock, no partial write
        assert _git(repo, "tag", "--list") == "v1.0.0"
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert completed and completed[0]["tag"] == "v2.0.0"


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
