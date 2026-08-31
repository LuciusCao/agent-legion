"""Ref-aware skill checkout for Agent dispatch and Host-side validation (#76).

``resolve_skill_checkout`` wraps ``SkillManager.checkout_skill`` with the
``resolve_workflow_skill`` contract check, and exposes the effective ref (the
declared source ref when the caller passed none) so manifests record the
exact pin the lock froze. ``checkout_node_skill`` adds the dispatch-time
source priority (node binding wins, Agent definition skill is the legacy
fallback). Kept out of ``skills/runtime.py`` for budget headroom.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from server.app.skills.manager import SkillManager
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.skills import resolve_workflow_skill
from server.app.workflows.workflow_node_skill import effective_node_skill


class SkillCheckout(NamedTuple):
    """Execution-private checkout plus the exact pin the lock froze."""

    key: str
    ref: str
    run_dir: Path
    commit: str
    version: str  # "ref@commit12"


def resolve_skill_checkout(
    skill_manager: SkillManager, key: str, execution_id: str, ref: str = ""
) -> SkillCheckout:
    """Checkout ``key`` at ``ref`` (the source default when empty), validated."""
    run_dir, commit, version = skill_manager.checkout_skill(key, execution_id, ref or None)
    try:
        # Validate the execution-private copy, not the shared cache: another
        # dispatch may switch the cache to a different ref right after this
        # checkout returns, while the run dir is the content actually
        # packaged/executed (codex P1 on PR 317). The run dir layout is
        # <runs_dir>/<execution_id>/<group>/<name>, so parents[1] is the root
        # under which resolve_workflow_skill joins the key.
        resolve_workflow_skill(run_dir.parents[1], key)
    except Exception:
        # #204 broad-except audit: cleanup-guard-then-bare-re-raise (#233
        # pattern — clean up broad, classify never). checkout_skill above
        # already copytree'd the execution-private run dir; whatever made the
        # contract validation fail (ValueError for the documented
        # missing/escaping-skill cases, OSError from the filesystem, or a
        # programming error), the private dir must be reclaimed before the
        # exception propagates or every retry leaks one runs/<execution_id>
        # copy (only the age-based sweeper would reclaim it). The bare
        # ``raise`` preserves the original type — the callers
        # (output_validation, the dispatch path) classify it themselves.
        skill_manager.cleanup_execution(execution_id)
        raise
    # version is "ref@commit12"; its ref prefix is the effective pin
    # (checkout_skill already fell back to the declared source ref).
    return SkillCheckout(key, version[:-13], run_dir, commit, version)


def checkout_node_skill(
    skill_manager: SkillManager, node: WorkflowNode, agent_skill: str, execution_id: str
) -> SkillCheckout:
    """Checkout the node's effective skill (see ``effective_node_skill``)."""
    key, ref = effective_node_skill(node, agent_skill)
    return resolve_skill_checkout(skill_manager, key, execution_id, ref)
