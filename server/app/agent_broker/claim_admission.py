"""Pre-lock candidate admission shared by the claim paths (#555).

Split out of ``claim_evaluate.py``: the filters that decide whether a scan
row is worth claiming WITHOUT taking any lock or writing anything —
workspace pause, manifest/contract parse, workspace ACL, dual-pool capacity,
runtime/model/label compatibility. Two callers:

- ``claim_evaluate.evaluate_candidate`` (single claim + batch write phase):
  admission runs inside the write transaction as the first gate;
- ``claim_batch_select`` (#555 read phase): the same admission runs on a
  read-only connection while selecting the batch, so the write phase never
  scans. Both must agree on the rules, hence the single home.

Everything here is pure with respect to the database: safe on a read-only
connection, and re-running it in the write phase is the revalidation the
two-phase claim relies on.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.agent_broker import agent_claim_compatibility
from server.app.agent_broker.claim_scan import ScanState, WorkerView, labels_satisfy
from server.app.agent_control.registry import CODE_PROTOCOL_VERSION

if TYPE_CHECKING:
    from server.app.agent_broker.broker import AgentExecutionBroker


def admit_candidate(
    broker: AgentExecutionBroker,
    selected: Mapping[str, Any],
    view: WorkerView,
    state: ScanState,
) -> dict[str, Any] | None:
    """Run the lock-free admission filters; return the manifest on admission.

    On a skip the reason lands in ``state.skip_reasons`` and None comes back.
    Passing admission does NOT claim anything — the row/state re-checks and
    the writes are ``evaluate_candidate``'s post-lock half.
    """
    selected_workspace = str(selected["workspace_id"])
    if selected_workspace not in state.pause_cache:
        check = broker.is_workspace_paused
        state.pause_cache[selected_workspace] = bool(check and check(selected_workspace))
    if state.pause_cache[selected_workspace]:
        # Paused workspace: keep the request queued for resume.
        state.skip_reasons["workspace_paused"] += 1
        return None
    kind = str(selected["kind"])
    # Code manifests are fully frozen at enqueue: no revision-time execution
    # re-resolution (the payload carries no provider/model).
    manifest: dict[str, Any]
    if kind == "code":
        manifest = json.loads(str(selected["manifest_json"]))
    else:
        try:
            manifest = agent_claim_compatibility.live_claim_manifest(selected)
        except ValueError:
            # Execution contract violation (EXEC-RUNTIME-DISPATCH-001): skip
            # here and keep the request queued — the unclaimable sweeper
            # fails it with the actionable message. Raising would 500 every
            # claim poll and head-of-line block the agent queue.
            state.skip_reasons["execution_contract_invalid"] += 1
            return None
    # Workspace admission scope from the server-side registration snapshot
    # (EXEC-WORKERACL-001): [] means all workspaces. Never trust Worker-
    # supplied fields for this.
    if view.allowed_workspaces and selected_workspace not in view.allowed_workspaces:
        state.skip_reasons["workspace_not_allowed"] += 1
        return None
    # Dual capacity pools: a candidate whose pool is exhausted is skipped,
    # not fatal — the other pool may still have claimable candidates.
    if kind == "code":
        # Defense in depth behind the register-time rejection: a v1 row
        # predating it must never hold code executions (v1 heartbeats carry
        # no cancel body, and old binaries cannot unpack code bundles).
        if view.protocol_version < CODE_PROTOCOL_VERSION:
            state.skip_reasons["protocol_version_too_old"] += 1
            return None
        if view.code_active >= view.code_capacity:
            state.skip_reasons["code_capacity_full"] += 1
            return None
        # Admission stops here (issue #284): protocol + code-pool capacity +
        # the workspace ACL above — capabilities no longer gate anything.
    else:
        if view.agent_active >= view.agent_capacity:
            state.skip_reasons["capacity_full"] += 1
            return None
        if selected["runtime"] not in view.runtimes:
            state.skip_reasons["runtime_mismatch"] += 1
            return None
        if not agent_claim_compatibility.worker_can_run(selected, manifest, view.models):
            state.skip_reasons["model_mismatch"] += 1
            return None
    if not labels_satisfy(
        view.labels, json.loads(selected["definition_json"]).get("requires_labels", {})
    ):
        state.skip_reasons["labels_mismatch"] += 1
        return None
    return manifest
