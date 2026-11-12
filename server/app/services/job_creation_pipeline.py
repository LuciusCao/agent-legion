"""JobCreationPipeline (#724): the single implementation of the creation sequence.

The sync /runs path (``run_service``) and both intake paths (sync
``/job-batches`` and the async intake queue) used to each write out the same
homologous sequence — dedup probe → candidate acceptance → node config
resolution → code-version freeze → deterministic run row → create_jobs_bulk
→ #501 compensation → scheduler notify → creation-event record — so every
semantic fix had to be written two (queue: three) times. This module hosts
each stage once; the paths differ only in where the values come from (items
vs intake resolver values, a fresh vs an already-queued run row), which the
callers pass in as parameters:

- dedup probe: the run path and the async queue point-probe this request's
  keys (``probe_existing_keys``, #467 A2); the legacy sync intake keeps its
  whole-workspace key load (``list_job_dedup_keys`` + the chunked
  ``resolve_fresh_candidates`` filter) at the call site — same
  workspace-scoped semantics, different cost shape.
- candidate resolution: items resolve through ``run_item_resolution`` (with
  text-item materialization, run path only); intake values resolve through
  the RESOLVERS registry. Both happen before the stages here.
- error presentation: ``intake_error_contract`` selects between the two
  pinned variants of the #501 compensation — the run path converts a
  pre-commit ValueError to the user-facing 400, the legacy intake keeps the
  original exception type.

Downstream semantics that must stay verbatim live BELOW this module and are
not reimplemented here: #1243's job-mutation lock domain (whole-batch
identity precheck, in-lock identity re-check before each chunk write,
post-write re-check, CREATE_CHUNK_ROWS commit granularity) and M36's
injective job-id encoding (#1255) are inside ``JobQueries.create_jobs_bulk``;
#501's two-branch compensation is
``run_partial_failure.compensate_partial_creation``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from server.app.services.agent_node_profile_catalog import legacy_agent_catalog
from server.app.services.job_errors import InvalidOperationError
from server.app.services.node_code_resolution import freeze_node_code_versions
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.services.run_partial_failure import (
    compensate_partial_creation,
    raise_compensated_partial_creation,
)
from server.app.settings import Settings

if TYPE_CHECKING:
    from server.app.events import JobEventManager
    from server.app.jobs import JobQueries

# Event-record chunk (#467 A5): the buffer itself batches revisions; this
# only bounds the transient id list per record call.
_EVENT_RECORD_CHUNK = 500


class JobCreationPipeline:
    """Shared creation stages over the JobQueries facade (BOUNDARY-DATA-001).

    The event sinks are the owning service's: the run path and the sync
    intake wire both buffer and manager, the async queue wires the buffer
    only (its manager-less record call is a no-op by construction).
    """

    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ):
        self.job_db = job_db
        self.settings = settings
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer

    def resolve_node_setup(
        self,
        definition: Any,
        workspace: dict[str, Any],
        workspace_id: str,
        workflow_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Node config resolution + code-version freeze.

        The ValueError → InvalidOperationError conversion of the config
        resolution lived at both call sites verbatim; it now lives here
        once. The freeze snapshots the published custom code versions into
        the run/batch payload so running jobs are immune to later edits
        (EXEC-CODE-002); empty when every node is builtin or the feature
        gate is off.
        """
        try:
            node_config = resolve_workflow_node_configs(
                definition,
                legacy_agent_catalog(self.job_db, workspace_id),
                workspace,
            )
        except ValueError as exc:
            raise InvalidOperationError(f"Invalid node configuration: {exc}") from exc
        node_code_versions = freeze_node_code_versions(
            self.job_db,
            self.settings.executor_runtime.workflows.custom_nodes_enabled,
            workspace_id,
            workflow_key,
            list(definition.executable_nodes),
        )
        return node_config, node_code_versions

    def probe_existing_keys(
        self,
        workspace_id: str,
        keys: Iterable[tuple[str, str]],
    ) -> set[tuple[str, str]]:
        """#467 A2: point lookups over the given keys (indexed IN probes)
        instead of loading the whole workspace's keys — same workspace-
        scoped semantics, cost tracks the submission size. #735 review P1:
        this probe is a check-then-act read — a concurrent submission can
        claim the same item after it; that race is closed on the write side
        (create_jobs_bulk's ownership clause + RETURNING), not by locking
        here, so the loser simply drops the raced item from its response.
        """
        return self.job_db.filter_existing_dedup_keys(workspace_id, keys)

    def collect_fresh_candidates(
        self,
        workspace_id: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Point-probe + filter: candidates whose (source_type, source_id)
        already has a job in this workflow are filtered out; accepted keys
        grow the set so intra-request duplicates filter exactly like
        pre-existing jobs."""
        existing_keys = self.probe_existing_keys(
            workspace_id,
            (
                (str(candidate["entity_type"]), str(candidate["entity_id"]))
                for candidate in candidates
            ),
        )
        fresh: list[dict[str, Any]] = []
        for candidate in candidates:
            key = (str(candidate["entity_type"]), str(candidate["entity_id"]))
            if key in existing_keys:
                continue
            existing_keys.add(key)
            fresh.append(candidate)
        return fresh

    def insert_jobs(
        self,
        *,
        candidates: list[dict[str, Any]],
        workflow_key: str,
        run_id: str,
        definition: Any,
        workspace_id: str,
        revision: dict[str, Any],
        node_config: dict[str, Any],
        notify: Callable[[], None] | None = None,
    ) -> list[str]:
        """create_jobs_bulk + notify, no compensation — the async intake
        queue's call shape (its chunk-level recovery is the queue cursor's
        job, not the two-branch compensation).

        ``notify`` is the caller module's ``notify_schedulable_work``
        reference, resolved at call time so the existing monkeypatch-based
        tests keep intercepting it; None skips the wake-up (the compensated
        creation caller notifies after its try/except instead).
        """
        job_ids = self.job_db.create_jobs_bulk(
            candidates=candidates,
            workflow_key=workflow_key,
            run_id=run_id,
            node_keys=list(definition.executable_nodes),
            workspace_id=workspace_id,
            revision=revision,
            frozen_config=node_config,
        )
        if job_ids and notify is not None:
            notify()
        return job_ids

    def create_run_and_jobs(
        self,
        *,
        workspace_id: str,
        workflow_key: str,
        source_kind: str,
        source_payload: dict[str, Any],
        node_code_versions: dict[str, Any],
        candidates: list[dict[str, Any]],
        revision: dict[str, Any],
        definition: Any,
        node_config: dict[str, Any],
        notify: Callable[[], None],
        intake_error_contract: bool = False,
    ) -> tuple[dict[str, Any], list[str]]:
        """create_run → create_jobs_bulk → #501 compensation → notify.

        The payload only derives the deterministic run id; the authoritative
        freeze lands on the run/jobs columns (RUN-FREEZE-001).
        ``intake_error_contract`` selects the legacy intake's pinned error
        presentation (the original exception type survives the empty-run
        branch — no ValueError → 400 conversion).
        """
        run = self.job_db.create_run(
            workflow_key,
            source_kind,
            source_payload,
            workspace_id=workspace_id,
            frozen_pins={"node_code_versions": node_code_versions},
        )
        try:
            # notify deliberately stays OUTSIDE the insert (below): a notify
            # failure must not trip the creation compensation.
            job_ids = self.insert_jobs(
                candidates=candidates,
                workflow_key=workflow_key,
                run_id=run["id"],
                definition=definition,
                workspace_id=workspace_id,
                revision=revision,
                node_config=node_config,
            )
        except Exception as exc:
            # #204 broad-except audit: compensate-then-re-raise after the
            # run row committed (create_jobs_bulk commits in chunks, so the
            # failure space is the bulk-insert's mixed psycopg/dedup
            # surface). The width is required because the compensation must
            # run on EVERY failure mode — ValueError (normalize-collision,
            # converted to the user-facing InvalidOperationError on the run
            # path) and unexpected errors alike — or a run row would linger;
            # nothing is masked: the non-converting branches re-raise
            # preserving type and traceback, and the compensation helper is
            # itself #204-audited to never mask this error.
            # A fresh item can still collide at insert time: under the #1255
            # injective encoding two items can only share a job id via the
            # same source_id under different source_types, or an item can
            # hit a legacy-path job whose pre-#1255 id coincides with the
            # new encoding. Chunked commits (#467 A3) change the outcome
            # only when a chunk already committed: the partial run STAYS
            # (delete_run_without_jobs's not-exists guard is a no-op) and is
            # marked failed with its progress so the operator sees what was
            # created; a resubmission resumes through the dedup filter.
            # With no committed chunk the compensation removes the run row
            # exactly like the pre-chunking shape. #501: the two-branch
            # compensation body lives ONCE in
            # run_partial_failure.compensate_partial_creation; the only
            # per-caller part left is the error presentation —
            # #467 review P1-2/P2-1: after a committed chunk EVERY failure
            # family maps to the structured partial-failure error (the
            # operator legibility requirement — created_so_far in the 400
            # detail; original rides as __cause__), with no committed chunk
            # the run branch keeps the pre-chunking semantics verbatim
            # (ValueError → 400, anything else → bare re-raise → 500) while
            # the intake branch keeps the original exception type.
            if intake_error_contract:
                raise_compensated_partial_creation(self.job_db, str(run["id"]), exc)
            partial_error = compensate_partial_creation(self.job_db, str(run["id"]), exc)
            if partial_error is not None:
                raise partial_error from exc
            if isinstance(exc, ValueError):
                raise InvalidOperationError(str(exc)) from exc
            raise
        if job_ids:
            notify()
        return run, job_ids

    def record_jobs_created(
        self,
        workspace_id: str,
        job_ids: list[str],
        *,
        jobs: list[dict[str, Any]] | None = None,
    ) -> None:
        """Creation-event record (#467 A5 chunked on the buffered path).

        The buffer is the batching layer (one lock acquisition + one
        revision per call, SSE payloads aggregated by the drain loop), so
        recording in bounded chunks keeps memory bounded for very large runs
        while the wire stays on the compacted batch path.

        ``jobs`` selects the legacy intake's bufferless fallback shape: full
        job rows + fresh stats (its API/SSE consumers still read rows). The
        run path's bufferless fallback broadcasts ids only with empty
        stats — the creation response no longer carries job rows (#467 A4),
        the manager's job_patch_batch consumers key off ids, and the
        aggregator's periodic flush owns the numbers on the buffered path.
        """
        if self.job_event_buffer is not None:
            for start in range(0, len(job_ids), _EVENT_RECORD_CHUNK):
                self.job_event_buffer.record_jobs_created(
                    workspace_id, job_ids[start : start + _EVENT_RECORD_CHUNK]
                )
        elif self.job_event_manager is not None:
            if jobs is not None:
                stats = self.job_db.count_jobs_by_status(workspace_id)
                self.job_event_manager.broadcast_jobs_created(workspace_id, jobs, stats)
            else:
                self.job_event_manager.broadcast_jobs_created(
                    workspace_id, [{"id": job_id} for job_id in job_ids], {}
                )
