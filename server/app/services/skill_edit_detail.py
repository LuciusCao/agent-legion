"""One editing operation owns its lock wait and all Git reads under one deadline."""

from pathlib import Path
from typing import Any

from filelock import Timeout

from server.app.services import skill_repo
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.skill_commit_snapshot import commit_snapshot
from server.app.services.skill_repo_edit import SkillEditValidationError, edit_lock_for
from server.app.services.skill_snapshot_git import SnapshotGit


def editing_detail(
    key: str, repo: Path, ref: str | None, base: Path, runs: Path | None
) -> dict[str, Any]:
    reader = SnapshotGit(repo)
    try:
        with edit_lock_for(repo, base, runs).acquire(timeout=reader.remaining()):
            if not skill_repo.is_git_repo(repo):
                raise NotFoundError(f"Skill {key!r} has no local git repository")
            target = f"refs/tags/{ref}^{{commit}}" if ref is not None else "HEAD^{commit}"
            # --quiet distinguishes a missing ref without exposing Git diagnostics.
            resolved = reader.run(
                ["rev-parse", "--verify", "--quiet", target], 128, missing_ok=True
            )
            if not resolved:
                raise NotFoundError(f"Skill {key!r} has no requested committed editing snapshot")
            commit = resolved.decode("ascii").strip()
            tags = reader.run(["tag", "--list", "--sort=-version:refname"], 1024 * 1024)
            return {
                "key": key,
                "ref": ref if ref is not None else "latest",
                "commit": commit,
                "available": True,
                "tags": tags.decode("utf-8", errors="replace").splitlines(),
                "files": commit_snapshot(repo, commit, reader),
            }
    except Timeout as exc:
        raise ConflictError("Editing snapshot is busy; retry") from exc
    except TimeoutError as exc:
        raise SkillEditValidationError(
            "Editing snapshot is busy or timed out",
            [{"path": ".", "error": "retry the editing snapshot"}],
        ) from exc
