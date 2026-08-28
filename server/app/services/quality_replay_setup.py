"""Copy-job construction and failure compensation for quality replays (#204).

Split from ``services/quality_replays.py`` for the file-size budget: this
module owns the setup half of a replay — building the isolated copy job on
frozen inputs, and compensating the half-created state when setup fails.
The exception layering lives here too: business failures (``JobServiceError``
family) are recorded as a failed replay row, programming errors are logged
with their traceback and leave no replay row behind (BOUNDARY-DATA-001: the
``quality_replays`` DELETE goes through the JobQueries facade
``delete_replay_if_active``, not hand-written service SQL).
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING, Any

from server.app.jobs.atomic_mutations import prepare_replay_copy
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_errors import InvalidOperationError, JobServiceError
from server.app.services.node_config_batch import frozen_node_config, run_frozen_payload
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from server.app.workflows.execution_control import ancestor_closure
from server.app.workflows.workflow_branching import downstream_nodes

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


class QualityReplaySetup:
    """Builds and compensates a replay's copy job (no routing/validation)."""

    def __init__(
        self,
        job_db: JobQueries,
        artifact_store: ArtifactStore | None,
    ) -> None:
        self.job_db = job_db
        self.artifact_store = artifact_store

    def build_copy_job(
        self,
        workspace_id: str,
        item: dict[str, Any],
        job: dict[str, Any],
        definition: WorkflowDefinition,
        node: WorkflowNode,
        replay_id: str,
        pin: dict[str, Any] | None,
    ) -> str:
        """Create the isolated copy job and set its node states atomically."""
        workflow_key = str(job["workflow_key"])
        revision = {
            "id": str(job["workflow_revision_id"] or ""),
            "version": int(job["workflow_version"] or 0),
            "definition_hash": str(job["workflow_definition_hash"] or ""),
            "definition_json": str(job["workflow_definition_snapshot_json"] or ""),
        }
        # Frozen intake state keeps the replay faithful to the original run.
        original_payload = run_frozen_payload(self.job_db, job)
        frozen = frozen_node_config(original_payload, node.key)
        quality_replay = {
            "replay_id": replay_id,
            "item_id": str(item["id"]),
            "source_job_id": str(job["id"]),
        }
        node_code_versions = (original_payload or {}).get("node_code_versions") or {}
        agent_versions = {node.key: pin} if pin is not None else {}
        # The digest payload mirrors the retired batch payload so the
        # deterministic run id is stable across the cutover; the authoritative
        # pins land on the run row and the frozen config on the copy job
        # (RUN-FREEZE-001).
        digest_payload = {
            "quality_replay": quality_replay,
            "node_config": {node.key: frozen} if frozen is not None else {},
            "node_code_versions": node_code_versions,
            "agent_versions": agent_versions,
        }
        batch = self.job_db.create_run(
            workflow_key,
            "quality_replay",
            digest_payload,
            workspace_id,
            frozen_pins={
                "quality_replay": quality_replay,
                "node_code_versions": node_code_versions,
                "agent_versions": agent_versions,
            },
        )
        candidate = {
            "entity_id": f"replay-{replay_id}",
            "entity_type": str(job["source_type"] or "question"),
            "title": f"Quality replay of {job['title'] or job['id']}",
            "stem": str(job["stem"] or ""),
        }
        try:
            copy_job = self.job_db.create_jobs_bulk(
                candidates=[candidate],
                workflow_key=workflow_key,
                run_id=str(batch["id"]),
                node_keys=list(definition.executable_nodes),
                workspace_id=workspace_id,
                revision=revision,
                frozen_config={node.key: frozen} if frozen is not None else {},
            )[0]
        except Exception:
            # #204: both business rejections (ValueError → 400 in the run
            # service) and programming errors land here; either way the
            # orphaned run row must go. The bare re-raise keeps the original
            # error type for the classification in create_replay above.
            # create_run committed before create_jobs_bulk ran; compensate the
            # orphaned run row like the items/sync-intake paths do.
            self.discard_empty_run(str(batch["id"]))
            raise
        copy_job_id = str(copy_job["id"])
        try:
            self._copy_frozen_inputs(job, copy_job, node)
            self._copy_artifact_refs(str(job["id"]), copy_job_id, definition, node.key)
            # The start node never enters job_nodes (EXEC-WORKFLOW-START-001), so it
            # must not reach prepare_replay_copy's completed_nodes either.
            ancestors = sorted(
                (ancestor_closure(definition, node.key) - {node.key})
                & definition.executable_nodes.keys()
            )
            downstream = sorted(downstream_nodes(definition, node.key))
            with self.job_db.write() as conn:
                prepare_replay_copy(
                    conn, copy_job_id, completed_nodes=ancestors, skipped_nodes=downstream
                )
        except Exception:
            # #204: mixed outcome space (InvalidOperationError from missing
            # frozen inputs, ValueError/JobServiceError from the shared
            # helpers, programming errors) — compensation must run for all of
            # them, so the catch stays broad; classification happens in
            # create_replay. The bare re-raise never converts one failure
            # kind into another.
            # Best-effort: the not-exists guard keeps the run once the copy
            # job exists, so this only cleans up if job creation rolled back.
            self.discard_empty_run(str(batch["id"]))
            # Never leave a fully-pending copy job behind: the scheduler would
            # run the whole workflow. Fail it so it drops out of the scan.
            with self.job_db.write() as conn:
                conn.execute(
                    "update jobs set status='failed',"
                    " error_message='quality replay setup failed',"
                    " updated_at=current_timestamp"
                    " where id = %s and status not in ('completed', 'failed')",
                    (copy_job_id,),
                )
            raise
        return copy_job_id

    def compensate_failed_setup(self, replay_id: str, exc: Exception) -> None:
        """Undo the replay row after ``build_copy_job`` failed (#204).

        Business failures are a normal outcome (the failed attempt is recorded
        as a replay row), so they mark the replay failed. Unexpected errors
        must not masquerade as replay business failures: the half-created
        replay row is removed (it would otherwise block retries at the
        one-active-replay guard) and the error is logged with its traceback
        for operators while the exception keeps propagating.
        """
        if isinstance(exc, JobServiceError):
            self._fail_replay(replay_id, f"replay setup failed: {exc}")
            return
        logger.error("Replay %s setup crashed", replay_id, exc_info=exc)
        self.job_db.delete_replay_if_active(replay_id)

    def discard_empty_run(self, run_id: str) -> None:
        # Best-effort cleanup of the run row after copy-job creation failed;
        # never mask the original failure. The broad catch (#204) is the
        # deliberate safety net here: cleanup must not replace the original
        # error, whatever it was, and the fallback warning (run id, failure
        # of the cleanup itself) is the actionable signal — the original
        # exception keeps propagating to the caller either way.
        try:
            self.job_db.delete_run_without_jobs(run_id)
        except Exception:
            logger.warning("run %s left orphaned after replay setup failed", run_id)

    def _copy_frozen_inputs(
        self, job: dict[str, Any], copy_job: dict[str, Any], node: WorkflowNode
    ) -> None:
        source_dir = resolve_job_dir(job, self.job_db.jobs_dir)
        target_dir = resolve_job_dir(copy_job, self.job_db.jobs_dir)
        missing = [name for name in node.inputs if not (source_dir / name).is_file()]
        if missing:
            raise InvalidOperationError(
                "frozen inputs are missing from the original job directory: "
                + ", ".join(sorted(missing))
            )
        for name in node.inputs:
            shutil.copy2(source_dir / name, target_dir / name)

    def _copy_artifact_refs(
        self, job_id: str, copy_job_id: str, definition: WorkflowDefinition, node_key: str
    ) -> None:
        """Share upstream artifact refs (same content hash) with the copy job."""
        if self.artifact_store is None:
            return
        ancestors = ancestor_closure(definition, node_key) - {node_key}
        for ref in self.artifact_store.refs_for_job(job_id):
            if ref["node_key"] in ancestors:
                self.artifact_store.add_ref(copy_job_id, ref["node_key"], ref["name"], ref["hash"])

    def _fail_replay(self, replay_id: str, message: str) -> None:
        with self.job_db.write() as conn:
            conn.execute(
                "update quality_replays set status = 'failed', error_message = %s,"
                " finished_at = current_timestamp where id = %s",
                (message, replay_id),
            )
