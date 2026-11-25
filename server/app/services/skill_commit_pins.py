"""Commit-level skill pins for quality replays (#322).

Ordinary dispatch is unchanged: ``latest`` resolves live HEAD and a concrete
ref resolves through the DB lock. A replay instead carries an independent
``skill_commits[node_key]`` frozen pin. Original-profile replays prove the
commit from the original completed execution; choosing a revision/draft
resolves that source at acceptance time. Only the latter uses ordinary
first-pin semantics for a previously unlocked concrete ref. Neither path
rewrites an existing lock entry, and exact-commit enqueue never writes one.

Legacy ``ref@commit12`` evidence is resolved only as a unique git object id,
never by re-reading the ref, lock, or current HEAD. Missing, malformed,
conflicting, or ambiguous evidence fails before replay rows are created.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import InvalidOperationError
from server.app.skills.commit_cache import (
    resolve_skill_commit,
    resolve_unique_skill_commit_prefix,
)
from server.app.skills.config import LATEST_REF
from server.app.skills.errors import SkillRepoError
from server.app.skills.runtime import build_skill_manager
from server.app.workflows.workflow_node_skill import effective_node_skill

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.skills.manager import SkillManager
    from server.app.workflows.schema import WorkflowNode

PIN_KEY = "skill_commits"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_LEGACY_PREFIX_RE = re.compile(r"^[0-9a-f]{12}$")


def skill_commit_pin(run_payload: Mapping[str, Any] | None, node_key: str) -> dict[str, Any] | None:
    """Read one node's independent replay commit pin from frozen run pins."""
    if not isinstance(run_payload, Mapping):
        return None
    pins = run_payload.get(PIN_KEY)
    if not isinstance(pins, Mapping):
        return None
    pin = pins.get(node_key)
    return dict(pin) if isinstance(pin, Mapping) else None


def capture_original_skill_pin(
    job_db: JobQueries,
    job: dict[str, Any],
    node: WorkflowNode,
    manager: SkillManager | None = None,
) -> dict[str, Any] | None:
    """Freeze the original execution's proven commit, or fail closed."""
    binding = _node_binding(node)
    if binding is None:
        return None
    skill_key, ref = binding
    record = job_db.latest_done_request_identities(str(job["id"]), [node.key]).get(node.key)
    if record is None:
        raise _proof_error(node.key, "no completed execution identity is recorded")
    full_commit, skill_version = record[2], record[3]
    if full_commit and not _COMMIT_RE.fullmatch(full_commit):
        raise _proof_error(node.key, "the recorded skill_commit is not a full 40-hex sha")

    prefix = ""
    if skill_version:
        version_ref, separator, prefix = skill_version.rpartition("@")
        if not separator or version_ref != ref:
            raise _proof_error(
                node.key,
                f"the recorded skill_version {skill_version!r} does not match ref {ref!r}",
            )
        if not _LEGACY_PREFIX_RE.fullmatch(prefix):
            raise _proof_error(
                node.key, "the recorded skill_version has no legacy 12-hex commit prefix"
            )
        if full_commit and not full_commit.startswith(prefix):
            raise _proof_error(
                node.key, "the recorded skill_commit and skill_version identify different commits"
            )
    if full_commit:
        return _pin(node, skill_key, ref, full_commit)
    if not prefix:
        raise _proof_error(
            node.key,
            "neither a full skill_commit nor a legacy ref@commit12 proof is recorded",
        )

    try:
        commit = resolve_unique_skill_commit_prefix(
            manager or build_skill_manager(job_db), skill_key, prefix
        )
    except SkillRepoError as exc:
        raise _proof_error(node.key, str(exc)) from exc
    return _pin(node, skill_key, ref, commit)


def resolve_selected_skill_pin(
    job_db: JobQueries, node: WorkflowNode, manager: SkillManager | None = None
) -> dict[str, Any] | None:
    """Resolve a chosen revision/draft node's skill at replay acceptance."""
    binding = _node_binding(node)
    if binding is None:
        return None
    skill_key, ref = binding
    try:
        commit = resolve_skill_commit(manager or build_skill_manager(job_db), skill_key, ref)
    except (SkillRepoError, ValueError) as exc:
        raise InvalidOperationError(
            f"cannot freeze the selected replay skill {skill_key!r} at ref {ref!r}: {exc}"
        ) from exc
    return _pin(node, skill_key, ref, commit)


def validated_skill_commit(
    node: WorkflowNode, skill_key: str, ref: str, pin: Mapping[str, Any]
) -> str:
    """Bind a frozen pin to this exact node snapshot before materialization."""
    expected = {"node_key": node.key, "skill_key": skill_key, "skill_ref": ref}
    for field, value in expected.items():
        if pin.get(field) != value:
            raise ValueError(
                f"replay skill pin {field}={pin.get(field)!r} does not match {value!r}"
            )
    commit = pin.get("skill_commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise ValueError("replay skill pin requires a full 40-hex skill_commit")
    return commit


def _node_binding(node: WorkflowNode) -> tuple[str, str] | None:
    try:
        key, ref = effective_node_skill(node, "")
    except ValueError:
        return None
    return key, ref or LATEST_REF


def _pin(node: WorkflowNode, skill_key: str, ref: str, commit: str) -> dict[str, Any]:
    if not _COMMIT_RE.fullmatch(commit):
        raise InvalidOperationError(
            f"replay skill resolution for node {node.key!r} did not produce a 40-hex commit"
        )
    return {
        "node_key": node.key,
        "skill_key": skill_key,
        "skill_ref": ref,
        "skill_commit": commit,
    }


def _proof_error(node_key: str, detail: str) -> InvalidOperationError:
    return InvalidOperationError(
        f"cannot prove the original skill commit for node {node_key!r}: {detail}; "
        "rerun the original node with current dispatch or choose a revision/draft"
    )
