"""Worker-path output validation: resolve the manifest's pinned skill to a
commit in the main process, then run materialization + validation on the
result-validate process pool (#569; #443 split this out of
``output_validation`` for the file-size budget).

Worker-reported success is untrusted: the Host revalidates server-side after
unpacking the result archive, against the exact skill content the execution
used (#330). Materialization is the shared (skill, commit) cache
(``skills.commit_cache``), so per-validation execution dirs are gone and
``cleanup_execution`` is no longer part of this path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.result_validate_pool import (
    validate_in_pool,
    validate_skill_commit_outputs,
)
from server.app.skills.commit_cache import resolve_skill_commit

if TYPE_CHECKING:
    from server.app.skills.manager import SkillManager


def validate_worker_outputs(
    skill_manager: SkillManager,
    manifest: dict[str, Any],
    job_dir: Path,
) -> str | None:
    """Run the manifest skill's validator against the unpacked Worker job dir
    (Worker-reported success is untrusted; same bar as the local path)."""
    skill = str(manifest.get("skill", ""))
    if not skill:
        return None
    try:
        commit = _manifest_commit(skill_manager, manifest, skill)
        verdict: str | None = validate_in_pool(
            validate_skill_commit_outputs,
            str(skill_manager.base_dir),
            str(skill_manager.runs_dir),
            tuple(skill_manager.git_command),
            skill,
            commit,
            str(job_dir),
        )
        return verdict
    except Exception as exc:
        # #204 broad-except audit: convert-to-contract, same channel as
        # run_output_validator's catch — the string verdict is the only
        # failure channel. The surface spans the commit resolution (the
        # DB-backed lock store, live-HEAD rev-parse), the pool hop (a broken
        # pool after the single rebuild-retry), and the pool task's
        # materialization/contract failures pickled back by reference
        # (SkillRepoError, ValueError) — a Worker-pinned skill that cannot be
        # materialized or validated is an untrusted-input outcome, not a
        # host bug, and must fail THIS node ("Validator error: ...") rather
        # than crash the completion path — the lease would otherwise expire
        # into the same poison manifest. The exception text rides the
        # message.
        return f"Validator error: {exc}"


def _manifest_commit(skill_manager: SkillManager, manifest: dict[str, Any], skill: str) -> str:
    # Main-process pin resolution (may touch the DB-backed lock document —
    # never in a pool worker): #330 manifests carry the full skill_commit,
    # which wins outright; legacy manifests resolve skill_ref (empty =
    # latest, #322).
    commit = str(manifest.get("skill_commit", ""))
    if commit:
        return commit
    return resolve_skill_commit(skill_manager, skill, str(manifest.get("skill_ref", "")) or None)
