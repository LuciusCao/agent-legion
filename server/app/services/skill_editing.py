"""Skill working-tree validation and draft-version authoring (issue #217).

``SkillEditingService`` backs the studio-agent skill tools: ``validate``
checks the runtime skill contract (``SKILL.md`` +
``references/output-contract.md`` + ``scripts/validate_output.py``, the
trio ``workflows/skills.py`` enforces at dispatch) against the skill's
content directory, and ``save_version`` writes a new skill version into
the source repository — local-path sources only, URL sources are
refused (pushing to a remote is an outward action).

Save is all-or-nothing and serialized (lock + checked rollback live in
``services/skill_repo_edit``); every input (paths, tag, repo state) is
validated before any file is written. The commit carries the platform
identity ``agent-legion-studio <studio@local>``, is tagged, and never
runs repo hooks (``--no-verify``: an automated authoring flow must not
execute user-supplied hook code). The DB skill lock is NEVER touched —
running jobs keep the locked commit until a human relocks.

Client error messages name the skill key only; host absolute paths go
to the server log (they would otherwise leak to scoped tokens and
workspace members).
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, Protocol

from server.app.services import skill_repo
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    JobServiceError,
    NotFoundError,
)
from server.app.services.skill_repo import SkillGitError
from server.app.services.skill_repo_edit import (
    edit_lock_for,
    rollback_checked,
    run_edit_git,
)
from server.app.skills.config import SkillsConfig, SkillsLock
from server.app.skills.skill_roots import default_skill_base_dir

logger = logging.getLogger(__name__)

STUDIO_GIT_AUTHOR_NAME = "agent-legion-studio"
STUDIO_GIT_AUTHOR_EMAIL = "studio@local"


class SkillStore(Protocol):
    def get_sources(self) -> SkillsConfig | None: ...

    def get_lock(self) -> SkillsLock | None: ...


class SkillEditValidationError(JobServiceError):
    """422-mapping edit rejection carrying a structured error list."""

    def __init__(self, message: str, errors: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.errors = errors


class SkillFileWrite(NamedTuple):
    path: str
    content: str


class SkillEditingService:
    def __init__(
        self,
        store: SkillStore,
        base_dir: Path | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        self._store = store
        self.base_dir = base_dir or default_skill_base_dir()
        # None resolves lazily to default_skills_runs_dir(); the route passes
        # settings.skills_runs_dir so the lock domain matches SkillManager's.
        self._runs_dir = runs_dir

    def validate(self, skill_key: str) -> dict[str, Any]:
        """Runtime contract check against the skill's content directory."""
        source = self._source(skill_key)
        content_dir = skill_repo.local_repo_path(source.repo) or self._cache_dir(skill_key)
        errors = self._contract_errors(content_dir)
        return {"key": skill_key, "valid": not errors, "errors": errors}

    def save_version(
        self,
        skill_key: str,
        files: list[SkillFileWrite],
        new_tag: str,
        message: str,
    ) -> dict[str, Any]:
        source = self._source(skill_key)
        repo_dir = skill_repo.local_repo_path(source.repo)
        if repo_dir is None:
            raise InvalidOperationError(
                f"Skill {skill_key!r} uses a remote URL source; only local path sources "
                "are editable from Studio"
            )
        with edit_lock_for(repo_dir, self.base_dir, self._runs_dir):
            return self._save_version_locked(skill_key, repo_dir, files, new_tag, message)

    def _save_version_locked(
        self,
        skill_key: str,
        repo_dir: Path,
        files: list[SkillFileWrite],
        new_tag: str,
        message: str,
    ) -> dict[str, Any]:
        if not skill_repo.is_git_repo(repo_dir):
            logger.error("skill %s source is not a git repo: %s", skill_key, repo_dir)
            raise NotFoundError(f"Skill {skill_key!r} source is not a git repository")
        head = skill_repo.head_commit(repo_dir)
        if head is None:
            raise InvalidOperationError(f"Skill {skill_key!r} repo has no commits yet")

        # Everything below validates BEFORE any write (all-or-nothing).
        self._check_tag(skill_key, repo_dir, new_tag)
        self._check_clean(skill_key, repo_dir)
        targets = self._resolve_targets(repo_dir, files)
        self._check_overwrites(repo_dir, targets)

        written_paths = [path for path, _ in targets]
        try:
            for path, content in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            contract_errors = self._contract_errors(repo_dir)
            if contract_errors:
                raise SkillEditValidationError(
                    "Skill contract validation failed after writing; the repo was rolled "
                    "back to its original commit",
                    contract_errors,
                )
            written = [path.relative_to(repo_dir).as_posix() for path in written_paths]
            self._git(repo_dir, ["add", "--", *written])
            self._git(
                repo_dir,
                [
                    "-c",
                    f"user.name={STUDIO_GIT_AUTHOR_NAME}",
                    "-c",
                    f"user.email={STUDIO_GIT_AUTHOR_EMAIL}",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "--no-verify",
                    "-m",
                    message,
                ],
            )
            self._git(repo_dir, ["tag", new_tag])
        except Exception:
            # All-or-nothing: any failure after the first write (contract
            # check, add, commit, tag) returns the repo to the recorded HEAD.
            rollback_checked(skill_key, repo_dir, head, written_paths, self._git)
            raise
        commit = skill_repo.head_commit(repo_dir)
        if commit is None:
            raise SkillGitError(f"Skill {skill_key!r} repo has no HEAD after commit")
        return {"key": skill_key, "tag": new_tag, "commit": commit, "files": written}

    # Validation helpers.

    def _source(self, skill_key: str):
        source = (self._store.get_sources() or SkillsConfig()).skills.get(skill_key)
        if source is None:
            raise NotFoundError(f"Skill {skill_key!r} is not configured")
        return source

    def _cache_dir(self, skill_key: str) -> Path:
        parts = skill_key.split("/")
        if len(parts) != 2 or not all(parts) or ".." in parts:
            raise NotFoundError("Invalid skill key")
        root = self.base_dir.resolve()
        candidate = (root / parts[0] / parts[1]).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise NotFoundError("Invalid skill path") from exc
        return candidate

    @staticmethod
    def _contract_errors(content_dir: Path) -> list[dict[str, str]]:
        if not content_dir.is_dir():
            return [{"path": ".", "error": "skill directory does not exist"}]
        errors: list[dict[str, str]] = []
        skill_md = content_dir / "SKILL.md"
        if not skill_md.is_file():
            errors.append({"path": "SKILL.md", "error": "missing SKILL.md"})
        elif not skill_md.read_text(encoding="utf-8", errors="replace").strip():
            errors.append({"path": "SKILL.md", "error": "SKILL.md is empty"})
        for required in ("references/output-contract.md", "scripts/validate_output.py"):
            if not (content_dir / required).is_file():
                errors.append({"path": required, "error": f"missing {required}"})
        return errors

    def _check_tag(self, skill_key: str, repo_dir: Path, new_tag: str) -> None:
        # `git check-ref-format refs/tags/-l` passes (the dash rule covers the
        # refname, not path components) while `git tag -l` would silently list
        # instead of creating — refuse dash-leading tags outright.
        if new_tag.startswith("-"):
            raise SkillEditValidationError(
                f"Invalid tag name: {new_tag!r}",
                [{"path": ".", "error": "tag names must not start with '-'"}],
            )
        fmt = self._git(repo_dir, ["check-ref-format", f"refs/tags/{new_tag}"], check=False)
        if fmt.returncode != 0:
            raise SkillEditValidationError(
                f"Invalid tag name: {new_tag!r}",
                [{"path": ".", "error": f"tag {new_tag!r} is not a valid git ref name"}],
            )
        if new_tag in skill_repo.list_tags(repo_dir):
            raise ConflictError(f"Skill {skill_key!r} repo already has tag {new_tag!r}")

    def _check_clean(self, skill_key: str, repo_dir: Path) -> None:
        status = self._git(repo_dir, ["status", "--porcelain"], check=False)
        if status.returncode != 0 or status.stdout.strip():
            raise ConflictError(
                f"Skill {skill_key!r} repo has uncommitted changes; commit or revert them first"
            )

    def _resolve_targets(
        self, repo_dir: Path, files: list[SkillFileWrite]
    ) -> list[tuple[Path, str]]:
        errors: list[dict[str, str]] = []
        targets: list[tuple[Path, str]] = []
        root = repo_dir.resolve()
        for raw, content in files:
            parts = PurePosixPath(raw).parts
            if (
                not raw
                or PurePosixPath(raw).is_absolute()
                or ".." in parts
                # Any level, any case: on case-insensitive filesystems
                # `.GIT/hooks/` still lands inside the git metadata dir.
                or any(part.lower() == ".git" for part in parts)
            ):
                errors.append(
                    {
                        "path": raw or ".",
                        "error": "path must be relative, stay inside the skill directory, "
                        "and not touch .git",
                    }
                )
                continue
            resolved = (root / raw).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                errors.append({"path": raw, "error": "path escapes the skill directory"})
                continue
            targets.append((resolved, content))
        if errors:
            raise SkillEditValidationError("Invalid skill file paths", errors)
        return targets

    def _check_overwrites(self, repo_dir: Path, targets: list[tuple[Path, str]]) -> None:
        """Refuse to overwrite a pre-existing UNTRACKED file: rolling back a
        write to such a file could not restore its original content."""
        errors: list[dict[str, str]] = []
        for path, _ in targets:
            relative = path.relative_to(repo_dir.resolve()).as_posix()
            tracked = self._git(
                repo_dir, ["ls-files", "--error-unmatch", "--", relative], check=False
            )
            if tracked.returncode != 0 and path.exists():
                errors.append(
                    {"path": relative, "error": "refusing to overwrite an untracked file"}
                )
        if errors:
            raise SkillEditValidationError("Unsafe skill file overwrite", errors)

    # Class attribute (not an import alias at module scope) so tests can
    # monkeypatch the git runner per service class.
    _git = staticmethod(run_edit_git)
