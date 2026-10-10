"""Classify whether a draft requires a new workflow revision.

Single source of truth (#1114): the publish path
(``workflow_revision_runtime.save_revision_runtime_or_publish``) and the draft
compare (``workflow_draft_compare.compare_workflow_draft`` →
``creates_revision``) both decide through ``revision_structurally_changed``.
Before #1114 the compare classified from its own field-name list and treated
every ``execution`` change as runtime-only, so a runtime-only draft promised
"no new revision" while publishing actually bumped the version.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.workflows.schema import WorkflowDefinition


def structural_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    payload = asdict(definition)
    # ``execution.runtime`` (#933) is NOT a runtime setting: it selects the
    # node's execution-profile source and is frozen with the revision/job
    # snapshot, so changing it must publish a new revision (in-flight jobs
    # keep the old one — PR #1039 codex R5). Only the remaining keys
    # (provider/model/thinking/prompt/prompt_mode) stay editable in place.
    # The loader already merged the top-level runtime default into every
    # agent node, so ``profile_runtime`` is the node's EFFECTIVE runtime: a
    # top-level default change is structural exactly when it moves some
    # node's effective runtime.
    for node in payload["nodes"].values():
        execution = node.pop("execution", None) or {}
        node["profile_runtime"] = execution.get("runtime", "")
    # Top-level execution defaults are runtime settings like the node-level
    # block: editing them updates the active revision in place instead of
    # publishing a structural revision (its runtime default is already baked
    # into every agent node above).
    payload.pop("execution", None)
    return payload


def revision_structurally_changed(current: WorkflowDefinition, draft: WorkflowDefinition) -> bool:
    """True when ``draft`` must publish a new revision over ``current``."""
    return structural_payload(current) != structural_payload(draft)
