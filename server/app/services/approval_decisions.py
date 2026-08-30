"""Human decisions on approval gates: append-only audit + node transitions.

The service behind the approval API (EXEC-APPROVAL-001). Every decision is
one immutable ``approval_decisions`` row (uniform actor format
``user:{id}``, never rewritten or removed — quality_labels idiom); the node
transition rides the same transaction through the JobQueries facade
(``jobs/queries/approval_decisions.py``), guarded on the node still being
``awaiting_approval`` so a concurrent decision or reset loses cleanly:

- ``approved``  → the gate completes; the decision payload is written as the
  ``{node_key}.approval.json`` job artifact first, so downstream conditional
  edges can branch on ``$.verdict`` and downstream ``inputs`` can require it.
- ``rework``    → the reviewer note (mandatory) is written as the gate's
  feedback artifact, then the target upstream node is reset through the
  regular rerun machinery — the gate itself goes stale with the rest of the
  downstream and re-parks once the upstream completes again.
- ``rejected``  → the gate fails, failing the job (its batch siblings are
  untouched).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.jobs import JobQueries
from server.app.jobs.queries.approval_decisions import ApprovalGateConflict
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.approval_node import (
    APPROVAL_NODE_TYPE,
    APPROVAL_VERDICTS,
)

logger = logging.getLogger(__name__)


class ApprovalDecisionService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        rerun: JobRerunService,
        *,
        object_store: Any = None,
    ) -> None:
        self.job_db = job_db
        self.settings = settings
        # The rerun service carries the SSE event channel (manager/buffer);
        # approvals broadcast through the same pair rather than re-plumbing it.
        self.rerun = rerun
        self.object_store = object_store

    # ── reads ───────────────────────────────────────────────────────────

    def list_decisions(self, workspace_id: str, job_id: str) -> list[dict[str, Any]]:
        """Full decision history for one job, newest first."""
        self._require_job(workspace_id, job_id)
        return self.job_db.list_approval_decisions(job_id)

    # ── the decision ────────────────────────────────────────────────────

    def decide(
        self,
        workspace_id: str,
        job_id: str,
        node_key: str,
        *,
        verdict: str,
        note: str = "",
        rework_target: str = "",
        decided_by: str = "",
    ) -> dict[str, Any]:
        if verdict not in APPROVAL_VERDICTS:
            raise InvalidOperationError(
                f"Unknown verdict {verdict!r}; expected one of {list(APPROVAL_VERDICTS)}"
            )
        job = self._require_job(workspace_id, job_id)
        definition = definition_from_job_snapshot(job) or require_workspace_active_definition(
            self.job_db, str(job["workspace_id"]), str(job["workflow_key"])
        )
        node = definition.nodes.get(node_key)
        if node is None or node.node_type != APPROVAL_NODE_TYPE:
            raise NotFoundError(f"Node {node_key} is not an approval node of this workflow")

        if verdict == "approved":
            return self._approve(job, node_key, note, decided_by)
        if verdict == "rejected":
            return self._reject(job, node_key, note, decided_by)
        return self._rework(job, definition, node, note, rework_target, decided_by)

    # ── verdict paths ───────────────────────────────────────────────────

    def _approve(
        self, job: dict[str, Any], node_key: str, note: str, decided_by: str
    ) -> dict[str, Any]:
        job_id = str(job["id"])
        decision = self._decision_row(job_id, node_key, "approved", note, "", decided_by)
        # The decision artifact lands before the transaction: a failed commit
        # leaves a harmless stale file (the gate stays awaiting and the next
        # decision overwrites it), while the reverse order could complete the
        # node with the artifact missing for downstream inputs.
        artifact_name = f"{node_key}.approval.json"
        self._write_job_artifact(job, artifact_name, decision)
        self._gate_transition(lambda: self.job_db.approve_gate_atomic(decision))
        self._upload_artifact(job, node_key, artifact_name)
        # Downstream nodes just became dispatchable — wake the poll loop.
        notify_schedulable_work()
        self._broadcast(job_id)
        return decision

    def _reject(
        self, job: dict[str, Any], node_key: str, note: str, decided_by: str
    ) -> dict[str, Any]:
        job_id = str(job["id"])
        decision = self._decision_row(job_id, node_key, "rejected", note, "", decided_by)
        message = f"rejected by reviewer: {note}" if note else "rejected by reviewer"
        self._gate_transition(lambda: self.job_db.reject_gate_atomic(decision, message))
        self._broadcast(job_id)
        return decision

    def _rework(
        self,
        job: dict[str, Any],
        definition: Any,
        node: Any,
        note: str,
        rework_target: str,
        decided_by: str,
    ) -> dict[str, Any]:
        from server.app.services.approval_rework import execute_rework

        return execute_rework(self, job, definition, node, note, rework_target, decided_by)

    # ── shared helpers ──────────────────────────────────────────────────

    def _require_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None or str(job["workspace_id"]) != workspace_id:
            raise NotFoundError("Job not found")
        return job

    @staticmethod
    def _gate_transition(write: Any) -> None:
        """Run one facade gate write, mapping its conflict to the HTTP error family."""
        try:
            write()
        except ApprovalGateConflict as exc:
            if exc.missing:
                raise NotFoundError(str(exc)) from exc
            raise ConflictError(str(exc)) from exc

    def _decision_row(
        self,
        job_id: str,
        node_key: str,
        verdict: str,
        note: str,
        rework_target: str,
        decided_by: str,
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "job_id": job_id,
            "node_key": node_key,
            "verdict": verdict,
            "note": note,
            "rework_target": rework_target,
            "decided_by": decided_by,
        }

    def _write_job_artifact(self, job: dict[str, Any], name: str, payload: dict[str, Any]) -> None:
        job_dir = resolve_job_dir(job, self.settings.jobs_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _upload_artifact(self, job: dict[str, Any], node_key: str, name: str) -> None:
        """Best-effort object-storage promotion, same stance as completion hooks."""
        if self.object_store is None or not getattr(self.object_store, "enabled", False):
            return
        try:
            self.object_store.upload(
                workspace_id=str(job["workspace_id"]),
                job_id=str(job["id"]),
                node_key=node_key,
                name=name,
                local_path=resolve_job_dir(job, self.settings.jobs_dir) / name,
            )
        except Exception:
            logger.warning(
                "approval artifact upload failed for job %s %s (local copy stays)",
                job["id"],
                name,
                exc_info=True,
            )

    def _broadcast(self, job_id: str) -> None:
        try:
            if self.rerun.job_event_buffer is not None:
                record_job_update(self.job_db, self.rerun.job_event_buffer, job_id)
            elif self.rerun.job_event_manager is not None:
                broadcast_job_update(self.job_db, self.rerun.job_event_manager, job_id)
        except Exception:
            logger.exception("Failed to broadcast approval event for job %s", job_id)
