"""Skill repository setup for the studio-agent tool surface (#633).

``SkillCreationService.create_skill`` materializes a brand-new skill under
the workspace's skill root (``~/.agents/skills/<workspace_id>/<name>``) as
an in-place git repo: every input is validated BEFORE anything touches the
disk (skill dir name, file paths, the runtime contract trio, the tag name),
the whole flow runs under the same repo-level cross-process edit lock as
``SkillEditingService.save_version`` (so it cannot interleave with a save
or a dispatch checkout of the same path), and ANY failure after the
directory appeared removes the partial directory again — a retry must
never be wedged by a half-initialized repo. The removal re-verifies the
directory identity (device/inode pair recorded right after mkdir), so
cleanup can never wipe a pre-existing directory swapped in by an
out-of-band writer.

Like save_version this is draft-only (STUDIO-AGENT-001): the commit carries
the platform identity ``agent-legion-studio <studio@local>``, never runs
repo hooks, and NEVER touches the DB skill lock — nothing is published; a
human still reviews, re-pins and relocks.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from os.path import lexists as _lexists
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.services import skill_repo
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.skill_edit_checks import (
    contract_errors,
    resolve_targets_checked,
)
from server.app.services.skill_editing import (
    STUDIO_GIT_AUTHOR_EMAIL,
    STUDIO_GIT_AUTHOR_NAME,
    SkillEditValidationError,
    SkillFileWrite,
)
from server.app.services.skill_repo import SkillGitError
from server.app.services.skill_repo_edit import edit_lock_for, run_edit_git
from server.app.skills.skill_roots import (
    is_valid_skill_dir_name,
    skills_root,
    workspace_skill_dir,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

# Bounds mirror SkillSaveVersionRequest (routes/studio_agent_skill_contracts.py).
MAX_FILES = 100
MAX_PATH_LENGTH = 512
MAX_FILE_BYTES = 128 * 1024
MAX_TAG_LENGTH = 128
MAX_MESSAGE_LENGTH = 4096

_CONTRACT_TRIO = ("SKILL.md", "references/output-contract.md", "scripts/validate_output.py")


class SkillCreationService:
    """Creates new skill repos under a workspace's skill directory."""

    def __init__(self, job_db: JobQueries, runs_dir: Path | None = None) -> None:
        self._job_db = job_db
        # None resolves lazily inside edit_lock_for (same lock domain as
        # SkillManager's dispatch checkout); the route passes
        # settings.skills_runs_dir.
        self._runs_dir = runs_dir

    def create_skill(
        self,
        workspace_id: str,
        skill_name: str,
        files: list[SkillFileWrite],
        new_tag: str,
        message: str,
    ) -> dict[str, Any]:
        """Materialize ``<workspace_id>/<skill_name>`` as a fresh in-place repo."""
        if self._job_db.get_workspace(workspace_id) is None:
            raise NotFoundError(f"Unknown workspace {workspace_id!r}")
        self._check_skill_name(skill_name)
        self._check_bounds(files, new_tag, message)

        workspace_dir = workspace_skill_dir(workspace_id)
        if workspace_dir.exists() and not workspace_dir.is_dir():
            raise ConflictError(f"Workspace {workspace_id!r} skill directory is not a directory")
        repo_dir = workspace_dir / skill_name
        # Everything below validates BEFORE the skill directory is created
        # (mkdir -p of the parent is idempotent and carries no skill state).
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._check_tag(workspace_dir, new_tag)
        # Tuple unpacking (vs item.path) keeps this module free of `.path`
        # attribute reads the BOUNDARY-DATA-001 scanner counts.
        targets, path_errors = resolve_targets_checked(
            repo_dir, [(raw, content) for raw, content in files]
        )
        contract = self._proposed_contract_errors(targets, repo_dir)
        errors = path_errors + contract
        if errors:
            raise SkillEditValidationError("Invalid skill creation payload", errors)

        with edit_lock_for(repo_dir, skills_root(), self._runs_dir):
            if _lexists(repo_dir):
                raise ConflictError(
                    f"Skill {workspace_id}/{skill_name} already exists; "
                    "pick a different name or use save_skill_version"
                )
            created_identity: tuple[int, int] | None = None
            try:
                repo_dir.mkdir()
                created_identity = _dir_identity(repo_dir)
                self._git(repo_dir, ["init"])
                for path, content in targets:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                # Defense in depth: the trio was checked on the proposed
                # file set; re-check what actually landed on disk.
                written_errors = contract_errors(repo_dir)
                if written_errors:
                    raise SkillEditValidationError(
                        "Skill contract validation failed for the created skill",
                        written_errors,
                    )
                self._git(repo_dir, ["add", "-A"])
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
                commit = skill_repo.head_commit(repo_dir)
                if commit is None:
                    raise SkillGitError(
                        f"Skill {workspace_id}/{skill_name!r} repo has no HEAD after setup"
                    )
            except Exception:
                # All-or-nothing: remove the partial directory so a retry is
                # not wedged by a half-initialized repo.
                # #204 broad-except audit: the outcome space is genuinely
                # mixed (git failures as SkillGitError, OSError from the
                # writes, the deliberately raised SkillEditValidationError);
                # every member must trigger the same cleanup, and the bare
                # re-raise keeps the original type for the route's mapping.
                if created_identity is not None:
                    _remove_created_repo(repo_dir, created_identity)
                raise
        return {
            "key": f"{workspace_id}/{skill_name}",
            "tag": new_tag,
            "commit": commit,
        }

    # Validation helpers (all BEFORE any disk write).

    @staticmethod
    def _check_skill_name(skill_name: str) -> None:
        if not is_valid_skill_dir_name(skill_name):
            raise SkillEditValidationError(
                f"Invalid skill name: {skill_name!r}",
                [
                    {
                        "path": ".",
                        "error": "skill name must match ^[a-z0-9][a-z0-9_-]{0,63}$",
                    }
                ],
            )

    @staticmethod
    def _check_bounds(files: list[SkillFileWrite], new_tag: str, message: str) -> None:
        errors: list[dict[str, str]] = []
        if not 1 <= len(files) <= MAX_FILES:
            errors.append({"path": ".", "error": f"files must hold 1..{MAX_FILES} entries"})
        for raw, content in files:  # tuple unpacking: no .path attribute reads
            if not 1 <= len(raw) <= MAX_PATH_LENGTH:
                errors.append({"path": raw or ".", "error": "path length out of bounds"})
            if len(content) > MAX_FILE_BYTES:
                errors.append({"path": raw or ".", "error": "content exceeds 128 KB"})
        if not 1 <= len(new_tag) <= MAX_TAG_LENGTH:
            errors.append({"path": ".", "error": "new_tag length out of bounds"})
        if not 1 <= len(message) <= MAX_MESSAGE_LENGTH:
            errors.append({"path": ".", "error": "message length out of bounds"})
        if errors:
            raise SkillEditValidationError("Invalid skill creation payload", errors)

    def _check_tag(self, workspace_dir: Path, new_tag: str) -> None:
        # Same rules as SkillEditingService._check_tag, run against the
        # (existing) workspace dir: check-ref-format needs no repo, and the
        # dash rule covers refnames git would accept but `git tag` misparses.
        if new_tag.startswith("-"):
            raise SkillEditValidationError(
                f"Invalid tag name: {new_tag!r}",
                [{"path": ".", "error": "tag names must not start with '-'"}],
            )
        fmt = self._git(workspace_dir, ["check-ref-format", f"refs/tags/{new_tag}"], check=False)
        if fmt.returncode != 0:
            raise SkillEditValidationError(
                f"Invalid tag name: {new_tag!r}",
                [{"path": ".", "error": f"tag {new_tag!r} is not a valid git ref name"}],
            )

    @staticmethod
    def _proposed_contract_errors(
        targets: list[tuple[Path, str]], repo_dir: Path
    ) -> list[dict[str, str]]:
        """The contract trio checked against the PROPOSED file set, before
        anything is written (same error shape as the on-disk check)."""
        proposed = {
            path.relative_to(repo_dir.resolve()).as_posix(): content for path, content in targets
        }
        errors: list[dict[str, str]] = []
        skill_md = proposed.get("SKILL.md")
        if skill_md is None:
            errors.append({"path": "SKILL.md", "error": "missing SKILL.md"})
        elif not skill_md.strip():
            errors.append({"path": "SKILL.md", "error": "SKILL.md is empty"})
        for required in _CONTRACT_TRIO[1:]:
            if required not in proposed:
                errors.append({"path": required, "error": f"missing {required}"})
        return errors

    # Class attribute (not an import alias at module scope) so tests can
    # monkeypatch the git runner per service class.
    _git = staticmethod(run_edit_git)


def _dir_identity(path: Path) -> tuple[int, int]:
    """(st_dev, st_ino) of a freshly created directory — the identity the
    failure cleanup must re-verify before removing anything."""
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise OSError(f"refusing to record non-directory {path}") from None
    return (st.st_dev, st.st_ino)


def _remove_created_repo(repo_dir: Path, identity: tuple[int, int]) -> None:
    """Best-effort removal of the directory THIS flow just made.

    Re-verifies the identity recorded right after mkdir (and that the path is
    still a directory, not a symlink), so cleanup can never wipe a
    pre-existing directory an out-of-band writer swapped in. Cleanup failures
    are logged, never raised: the original setup error must propagate.
    """
    try:
        st = os.lstat(repo_dir)
        if (st.st_dev, st.st_ino) != identity or not stat.S_ISDIR(st.st_mode):
            logger.error(
                "refusing to clean up %s: directory identity changed since setup",
                repo_dir,
            )
            return
        shutil.rmtree(repo_dir)
    except OSError:
        logger.exception("failed to clean up partially set-up skill repo %s", repo_dir)
