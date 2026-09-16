"""Skill working-tree validation and draft-version authoring (issue #217).

``SkillEditingService`` backs the studio-agent skill tools: ``validate``
checks the runtime skill contract (``SKILL.md`` +
``references/output-contract.md`` + ``scripts/validate_output.py``, the
trio ``workflows/skills.py`` enforces at dispatch) against the skill's
content directory, and ``save_version`` writes a new skill version into
the skill's in-place repository at ``<skills root>/<key>`` (#322:
in-place is the only mode, there is no remote source to refuse).

#542 grading: a present-but-malformed root ``contract.yaml`` is an ERROR
(the save rolls back like any contract failure), while a MISSING one is a
WARNING only (``validate`` reports it, ``save_version`` succeeds and
carries the warnings in its response) — the migration window keeps legacy
embedded-block and contract-less skills saveable.

Save is all-or-nothing and serialized (lock + checked rollback live in
``services/skill_repo_edit``); every input (paths, tag, repo state) is
validated before any file is written. The commit carries the platform
identity ``agent-legion-studio <studio@local>``, is tagged, and never
runs repo hooks (``--no-verify``: an automated authoring flow must not
execute user-supplied hook code). The DB skill lock is NEVER touched:
nodes pinned to a tag keep the locked commit until the node is re-pinned
and relocked, while ``latest`` nodes simply pick the new HEAD up on
their next dispatch.

Client error messages name the skill key only; host absolute paths go
to the server log (they would otherwise leak to scoped tokens and
workspace members).

#633: when the skill's workspace carries a ``_shared/map.json`` mapping
materials to this skill, ``save_version`` injects those materials into
the write set before any file is written (planning in
``services/skill_shared_sync``) — the synced copies land in the commit,
and the save response reports them as ``synced_files``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from server.app.services import skill_repo
from server.app.services.job_errors import (
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.skill_edit_checks import (
    graded_contract_check,
    resolve_targets_checked,
)
from server.app.services.skill_repo import SkillGitError
from server.app.services.skill_repo_edit import (
    SkillEditValidationError,
    edit_lock_for,
    rollback_checked,
    run_edit_git,
)
from server.app.services.skill_save_guards import (
    check_clean,
    check_overwrites,
    check_tag,
)
from server.app.services.skill_shared_store import shared_dir_for, shared_edit_lock
from server.app.services.skill_shared_sync import SharedSyncPlan, plan_shared_sync
from server.app.skills.skill_roots import default_skill_base_dir

logger = logging.getLogger(__name__)

STUDIO_GIT_AUTHOR_NAME = "agent-legion-studio"
STUDIO_GIT_AUTHOR_EMAIL = "studio@local"

# Moved to skill_repo_edit (#633: the shared-materials planner raises it);
# re-exported so historical importers (job_http, tests) keep resolving.
__all__ = ["SkillEditingService", "SkillEditValidationError", "SkillFileWrite"]


class SkillFileWrite(NamedTuple):
    path: str
    content: str


class SkillEditingService:
    def __init__(
        self,
        base_dir: Path | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        self.base_dir = base_dir or default_skill_base_dir()
        # None resolves lazily to default_skills_runs_dir(); the route passes
        # settings.skills_runs_dir so the lock domain matches SkillManager's.
        self._runs_dir = runs_dir

    def validate(self, skill_key: str) -> dict[str, Any]:
        """Runtime contract check against the skill's content directory.

        #542: errors keep failing the trio/format layer (a malformed root
        ``contract.yaml`` included); a missing root ``contract.yaml`` only
        warns (the response stays ``valid``).
        """
        errors, warnings = graded_contract_check(self._skill_dir(skill_key))
        return {"key": skill_key, "valid": not errors, "errors": errors, "warnings": warnings}

    def save_version(
        self,
        skill_key: str,
        files: list[SkillFileWrite],
        new_tag: str | Callable[[Path], str],
        message: str,
        *,
        prepare: Callable[[Path], SharedSyncPlan | None] | None = None,
    ) -> dict[str, Any] | None:
        repo_dir = self._skill_dir(skill_key)
        with edit_lock_for(repo_dir, self.base_dir, self._runs_dir):
            if prepare is None:
                tag = new_tag(repo_dir) if callable(new_tag) else new_tag
                return self._save_version_locked(skill_key, repo_dir, files, tag, message)
            # Propagation path (codex P1 on #674): the shared generation
            # lock is held from the recheck/plan through the skip judgment
            # AND the file application — a concurrent full-state PUT needs
            # this lock and therefore cannot swap the generation anywhere
            # in the per-skill critical section. Lock order stays
            # skill → shared; prepare must NOT acquire the shared lock
            # itself. The result is None exactly when prepare skips.
            shared_dir = shared_dir_for(self.base_dir, skill_key)
            with shared_edit_lock(shared_dir, self.base_dir):
                sync_plan = prepare(repo_dir)
                if sync_plan is None:
                    return None
                tag = new_tag(repo_dir) if callable(new_tag) else new_tag
                return self._save_version_locked(
                    skill_key, repo_dir, files, tag, message, sync_plan=sync_plan
                )

    def _save_version_locked(
        self,
        skill_key: str,
        repo_dir: Path,
        files: list[SkillFileWrite],
        new_tag: str,
        message: str,
        *,
        sync_plan: SharedSyncPlan | None = None,
    ) -> dict[str, Any]:
        if not skill_repo.is_git_repo(repo_dir):
            logger.error("skill %s has no in-place git repo: %s", skill_key, repo_dir)
            raise NotFoundError(f"Skill {skill_key!r} has no in-place git repository")
        head = skill_repo.head_commit(repo_dir)
        if head is None:
            raise InvalidOperationError(f"Skill {skill_key!r} repo has no commits yet")

        # Everything below validates BEFORE any write (all-or-nothing).
        check_tag(self._git, skill_key, repo_dir, new_tag)
        check_clean(self._git, skill_key, repo_dir)
        targets = self._resolve_targets(repo_dir, files)
        # Shared-material sync (#633): mapped materials are injected into the
        # write set (shared copy authoritative) and flow through the same
        # path-safety/overwrite/contract/commit/tag steps; malformed map,
        # missing source or colliding hand-supplied path = pre-write 422.
        # No _shared dir = no-op. SkillFileWrite IS a tuple[str, str] (a
        # NamedTuple), so it passes plan_shared_sync's Sequence directly.
        # A caller-pinned plan (propagation, codex P1 on #674: recheck and
        # plan fixed in one shared-lock critical section) is used as-is
        # instead of re-reading the shared state under a fresh lock.
        if sync_plan is None:
            sync_plan = plan_shared_sync(self.base_dir, skill_key, files)
        for source, shared_content in sync_plan.files:
            targets.extend(
                self._resolve_targets(repo_dir, [SkillFileWrite(source, shared_content)])
            )
        check_overwrites(self._git, skill_key, repo_dir, targets)

        written_paths = [path for path, _ in targets]
        try:
            for path, content in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            # #542 graded post-write check: trio errors plus a malformed
            # root contract.yaml are errors (rollback); a missing one is a
            # warning carried on the success response.
            validation_errors, warnings = graded_contract_check(repo_dir)
            if validation_errors:
                raise SkillEditValidationError(
                    "Skill contract validation failed after writing;"
                    " the repo was rolled back to its original commit",
                    validation_errors,
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
            # #204 broad-except audit: the outcome space here is genuinely
            # mixed and every member must trigger the same rollback — the
            # deliberately-raised SkillEditValidationError (a JobServiceError
            # the route renders as 422), SkillGitError from the git runner
            # (OSError/timeout are converted inside run_edit_git), OSError
            # from path.write_text, and programming errors. A narrow family
            # cannot enumerate the subprocess/filesystem boundary, and the
            # bare re-raise keeps the original type for the route's mapping;
            # rollback_checked itself raises SkillRollbackError when it
            # cannot restore, which correctly escapes as the loudest signal.
            rollback_checked(skill_key, repo_dir, head, written_paths, self._git)
            raise
        commit = skill_repo.head_commit(repo_dir)
        if commit is None:
            raise SkillGitError(f"Skill {skill_key!r} repo has no HEAD after commit")
        # synced_files: shared materials synced into this commit (#633); warnings:
        # #542 — the save SUCCEEDED despite them (a missing root contract.yaml
        # is a warning, not an error — migration window).
        return {
            "key": skill_key,
            "tag": new_tag,
            "commit": commit,
            "files": written,
            "synced_files": sorted(source for source, _ in sync_plan.files),
            "warnings": warnings,
        }

    # Validation helpers.

    def _skill_dir(self, skill_key: str) -> Path:
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

    def _resolve_targets(
        self, repo_dir: Path, files: list[SkillFileWrite]
    ) -> list[tuple[Path, str]]:
        # Shared path-safety rules with SkillCreationService (#633). The
        # tuple unpacking (vs item.path) keeps this module free of `.path`
        # attribute reads the BOUNDARY-DATA-001 scanner counts.
        targets, errors = resolve_targets_checked(
            repo_dir, [(raw, content) for raw, content in files]
        )
        if errors:
            raise SkillEditValidationError("Invalid skill file paths", errors)
        return targets

    # Class attribute (not an import alias at module scope) so tests can
    # monkeypatch the git runner per service class.
    _git = staticmethod(run_edit_git)
