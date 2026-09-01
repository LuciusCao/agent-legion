"""Ref-aware skill checkout for Agent dispatch and Host-side validation (#76, #330)."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from server.app.skills.manager import SkillManager
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.skills import resolve_workflow_skill
from server.app.workflows.workflow_node_skill import effective_node_skill


class SkillCheckout(NamedTuple):
    key: str
    ref: str
    run_dir: Path
    commit: str
    version: str  # "ref@commit12"

    def manifest_pins(self) -> dict[str, str]:
        """The manifest pin quad (skill/skill_version/skill_ref/skill_commit)."""
        return {
            "skill": self.key,
            "skill_version": self.version,
            "skill_ref": self.ref,
            "skill_commit": self.commit,
        }


def validate_run_dir(manager: SkillManager, key: str, execution_id: str, run_dir: Path) -> None:
    """Contract-check the materialized run dir; reclaim it on failure."""
    try:
        # Validate the execution-private copy: the run dir is the content
        # actually packaged/executed. Layout <runs_dir>/<exec>/<group>/<name>,
        # so parents[1] is the root resolve_workflow_skill joins the key under.
        resolve_workflow_skill(run_dir.parents[1], key)
    except Exception:
        # #204 broad-except audit: cleanup-guard-then-bare-re-raise (#233
        # pattern — clean up broad, classify never). The materialized run dir
        # must be reclaimed whatever made the contract validation fail
        # (ValueError for the documented missing/escaping-skill cases, OSError
        # from the filesystem, or a programming error), or every retry leaks
        # one runs/<execution_id> copy (only the age-based sweeper would
        # reclaim it). The bare ``raise`` preserves the original type — the
        # callers (output_validation, the dispatch path) classify it.
        manager.cleanup_execution(execution_id)
        raise


def resolve_skill_checkout(
    skill_manager: SkillManager, key: str, execution_id: str, ref: str = ""
) -> SkillCheckout:
    """Checkout ``key`` at ``ref`` (empty/``latest`` = live HEAD), validated."""
    run_dir, commit, version = skill_manager.checkout_skill(key, execution_id, ref or None)
    validate_run_dir(skill_manager, key, execution_id, run_dir)
    # version is "ref@commit12"; its ref prefix is the effective pin
    # (checkout_skill already normalized an empty ref to "latest").
    return SkillCheckout(key, version[:-13], run_dir, commit, version)


def checkout_node_skill(
    skill_manager: SkillManager, node: WorkflowNode, agent_skill: str, execution_id: str
) -> SkillCheckout:
    """Checkout the node's effective skill (see ``effective_node_skill``)."""
    key, ref = effective_node_skill(node, agent_skill)
    return resolve_skill_checkout(skill_manager, key, execution_id, ref)
