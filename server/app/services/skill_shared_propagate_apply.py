"""Per-skill apply step of shared-material propagation (issue #673).

Split from ``skill_shared_propagate`` for the file-size budget: the
locked critical section around one skill's save — generation re-verify
(codex P1), skip re-judgment and in-lock tag selection (codex P2), and
the per-skill outcome isolation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.services import skill_repo
from server.app.services.job_errors import ConflictError, JobServiceError
from server.app.services.skill_editing import SkillEditingService
from server.app.services.skill_shared_propagate_plan import (
    PropagateSkillResult,
    SharedGeneration,
    next_repo_tag,
)
from server.app.services.skill_shared_propagate_plan import (
    generation_matches as _generation_matches,
)
from server.app.services.skill_shared_store import shared_edit_lock
from server.app.skills.skill_roots import workspace_skill_dir

logger = logging.getLogger(__name__)


class GenerationConflictError(Exception):
    """Internal marker: the shared generation changed mid-batch. Converted
    to a retryable ``ConflictError`` (409) for the whole request — it must
    NOT degrade into a per-skill failure (and save_version raises its own
    ConflictError for a dirty tree, which IS per-skill)."""


def _head_matches(repo_dir: Path, source: str, shared_bytes: bytes) -> bool:
    result = skill_repo.run_git(repo_dir, ["show", f"HEAD:{source}"], check=False)
    return result.returncode == 0 and result.stdout == shared_bytes


_CONCURRENCY_DETAIL = "版本已被并发更新，请刷新后重试（数据未受影响）"

# save_version 的 commit 调用以 "-c" 身份配置开头，SkillGitError 按
# args[0] 命名为 "git -c failed ..."；tag 步骤则是 "git tag failed ..."
# —— 这两类与 "already has tag" 都是并发竞争（edit-lock 域外的并发写）
# 的典型失败，改写为可操作的友好文案。
_CONCURRENCY_GIT_FAILURES = (
    "git -c failed for the skill repository",
    "git tag failed for the skill repository",
)


def _friendly_detail(exc: JobServiceError) -> str:
    """Rewrite concurrency-typical save failures (tag conflict /
    nothing-to-commit from a racing save) into an actionable message; the
    underlying semantics (per-skill failed, no data loss) are unchanged."""
    text = str(exc)
    if "already has tag" in text or text in _CONCURRENCY_GIT_FAILURES:
        return _CONCURRENCY_DETAIL
    return text


def propagate_one(
    workspace_id: str,
    skill: str,
    mapped_sources: tuple[str, ...],
    shared_dir: Path,
    generation: SharedGeneration,
    shared_bytes: dict[str, bytes],
    editing: SkillEditingService,
) -> PropagateSkillResult:
    skill_key = f"{workspace_id}/{skill}"
    repo_dir = workspace_skill_dir(workspace_id, base_dir=editing.base_dir) / skill
    if not skill_repo.is_git_repo(repo_dir):
        return PropagateSkillResult(skill=skill, status="skipped", detail="skill repo not found")
    missing = next((s for s in mapped_sources if s not in shared_bytes), None)
    if missing is not None:
        return PropagateSkillResult(
            skill=skill, status="failed", detail=f"shared source unreadable: {missing}"
        )

    def _recheck_and_skip(repo: Path) -> bool:
        # Runs INSIDE the skill repo lock (codex P2's critical section);
        # the shared lock nests within it, preserving the skill → shared
        # lock order. Generation first (codex P1): a mid-batch PUT swap is
        # a retryable conflict for the whole batch, not a per-skill skip.
        with shared_edit_lock(shared_dir, editing.base_dir):
            if not _generation_matches(shared_dir, generation):
                raise GenerationConflictError
        return all(_head_matches(repo, source, shared_bytes[source]) for source in mapped_sources)

    message = f"Sync shared materials: {', '.join(mapped_sources)}"
    try:
        outcome = editing.save_version(
            skill_key,
            [],
            # Tag selection inside the repo lock (codex P2): the waiter in
            # a race re-reads the winner's tags instead of colliding.
            next_repo_tag,
            message,
            skip_if=_recheck_and_skip,
        )
    except GenerationConflictError as exc:
        # Generation changed mid-batch — abort with a retryable 409 for the
        # whole request instead of applying a stale plan (codex P1).
        raise ConflictError(
            "Shared materials changed during propagation; retry the request"
        ) from exc
    except JobServiceError as exc:
        # Mapped save failures (dirty tree 409, contract regression 422,
        # tag conflict, git operational error) — isolated to this skill.
        # Concurrency-typical ones (tag conflict / nothing-to-commit from a
        # racing save OUTSIDE the edit-lock domain) get a user-friendly
        # detail: the data is intact, a refresh-and-retry is the fix.
        return PropagateSkillResult(skill=skill, status="failed", detail=_friendly_detail(exc))
    except Exception as exc:
        # #204 broad-except audit: per-skill isolation must hold for ANY
        # save failure mode, including ones outside the JobServiceError
        # taxonomy (e.g. SkillRollbackError after a failed rollback or a
        # programming error). Swallowing into a per-skill `failed` result
        # is the batch contract — one repo's problem must not strand the
        # others; the full traceback goes to the server log, the client
        # gets the exception type only (messages may carry host paths).
        logger.exception("shared-material propagate failed for skill %s", skill_key)
        return PropagateSkillResult(
            skill=skill, status="failed", detail=f"unexpected error ({type(exc).__name__})"
        )
    if outcome is None:
        return PropagateSkillResult(skill=skill, status="skipped", detail="already in sync")
    return PropagateSkillResult(
        skill=skill,
        status="synced",
        tag=str(outcome["tag"]),
        synced_files=tuple(outcome["synced_files"]),
    )
