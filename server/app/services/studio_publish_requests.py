"""Agent-initiated workflow publish requests (issue #416): service layer.

Two actors, one handshake:

- **Agent** (studio-agent scoped token, via the ``request_workflow_publish``
  MCP tool): validates the workspace's unpublished draft against the FULL
  publish validation set and parks a pending request. It never publishes —
  no code path from the tool surface reaches the revision pipeline.
- **Human** (full session, via the Studio confirm endpoint): the confirm
  action claims the row (pending → ``confirming``), replays the exact
  ``publish_workflow_draft`` gates the Studio publish button uses
  (key-match guard included) against the workspace's draft-store YAML, then
  records the resulting revision on the request row.
  A draft that drifted (edited or invalidated after the agent's request)
  fails the same gates and raises — the request returns to pending so the
  human can fix the draft and confirm again, or cancel.

Shared helpers (wire payload, expiry comparison, the draft-version token)
live in studio_publish_request_support.py; the poll-side read (pending /
live-confirming surfacing, the stale-claim sweeps) in
studio_publish_request_poll.py (#429 四轮 split, file budget).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import (
    ConflictError,
    NotFoundError,
)
from server.app.services.studio_agent_tools import studio_agent_created_by
from server.app.services.studio_publish_request_poll import poll_pending_request
from server.app.services.studio_publish_request_support import (
    active_revision_id,
    draft_yaml_hash,
    is_past_expiry,
    iso_payload,
    may_read_request,
    refuse_stale_draft_claim,
    workspace_draft_yaml,
)
from server.app.services.workflow_draft_key import require_draft_workflow_key_match
from server.app.services.workflow_draft_publish import (
    publish_workflow_draft,
    validate_workflow_draft_for_publish,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.settings import Settings

logger = logging.getLogger(__name__)


class StudioPublishRequestService:
    """The agent→human publish handshake over studio_publish_requests."""

    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self._job_db = job_db
        self._settings = settings

    # -- agent side (tool surface) ---------------------------------------

    def request_publish(
        self,
        workspace_id: str,
        user: dict[str, Any],
        chat_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Park a pending publish request for the workspace's draft.

        The draft must pass the full publish validation set — an invalid
        draft creates no request (the agent fixes it first, same loop as
        validate_workflow). Never produces a revision. The request binds
        the CURRENT server draft's hash (#429 三轮 P1-3): the human's
        confirm publishes that exact draft or refuses with 409.
        """
        if self._job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
        draft_yaml = workspace_draft_yaml(self._job_db, workspace_id)
        errors = validate_workflow_draft_for_publish(
            self._job_db,
            workspace_id,
            draft_yaml,
            self._settings.executor_runtime.workflows.custom_nodes_enabled,
        )
        if errors:
            raise ConflictError(
                "Draft has validation errors; fix them before requesting publish: "
                + "; ".join(errors[:5])
            )
        # The confirming guard lives INSIDE create's transaction now
        # (#429 四轮 codex P1 — it shares the advisory lock with the claim,
        # so "no confirming" cannot go stale between the check and the
        # INSERT; a stale confirming row is swept there in the same
        # transaction). A live claim surfaces here as the familiar 409.
        # Attribution mirrors the draft tools: the run's initiating user
        # behind the studio-agent prefix (STUDIO-AGENT-001 §0.4).
        request = self._job_db.create_pending_publish_request(
            workspace_id,
            studio_agent_created_by(str(user["id"])),
            chat_session_id,
            draft_hash=draft_yaml_hash(draft_yaml),
        )
        return iso_payload(request)

    def get_request_status(self, request_id: str, user: dict[str, Any]) -> dict[str, Any]:
        """One request by id for the agent; authorization mirrors
        build_session_context (bound token → workspace match, unbound →
        workspace membership; mismatches 404 so foreign ids cannot probe)."""
        request = self._job_db.get_publish_request(request_id)
        if request is None or not may_read_request(self._job_db, request, user):
            raise NotFoundError("Publish request not found")
        return iso_payload(request)

    # -- human side (Studio endpoints) ------------------------------------

    def get_pending(self, workspace_id: str) -> dict[str, Any] | None:
        """The workspace's live request for the poll (None when there is
        none): the pending row, or the ``confirming`` row while its publish
        is in flight. The orchestration (live-confirming surfacing, the
        stale-claim sweep, the healthy-poll zero-write discipline) lives in
        studio_publish_request_poll.py — see poll_pending_request."""
        return poll_pending_request(self._job_db, workspace_id)

    def confirm(self, workspace_id: str, request_id: str) -> dict[str, Any]:
        """Human confirm: publish the draft through the manual-publish gates.

        #429 三轮 P1-2 (cancel race): the FIRST step is the atomic claim —
        the row moves pending → ``confirming`` atomically (TTL and draft
        hash re-checked in the same statement (#429 三轮 P1-3). From that
        moment cancel cannot touch the row (its predicate matches pending
        only), so "user cancelled but the revision still went live" is
        impossible: either the cancel landed first (the claim finds no
        pending row and the confirm 404s) or the claim landed first (the
        cancel 404s and the publish proceeds). A new agent request in the
        window is refused instead of superseding the confirming row.

        The publish itself is the same ``publish_workflow_draft`` call the
        Studio publish endpoint makes; the request row moves to
        ``confirmed`` only after that call succeeded, recording the
        resulting revision. A refused publish rolls the row back to
        ``pending`` (retryable, cancellable) — never resolves it.

        TOCTOU hardening (#429): the resolve step no longer checks
        ``expires_at``. The claim already checked the TTL, and the publish
        between them may legitimately outlive the remaining TTL — a revision
        that landed must be recorded as confirmed, never denied by a TTL
        that expired mid-publish. The ``status='confirming'`` predicate
        alone still guards double-resolution.
        """
        draft = self._job_db.get_workspace_workflow_draft(workspace_id)
        if draft is None:
            # No draft store: nothing to publish — the request cannot be
            # confirmed regardless (404 keeps the unknown/missing-request
            # answers uniform; a live pending request with no draft is the
            # same dead end, and the poll/claim state stays coherent).
            raise NotFoundError("Publish request not found or already resolved")
        draft_yaml = str(draft["definition_yaml"])
        claimed = self._job_db.claim_pending_publish_request(
            workspace_id, request_id, draft_yaml_hash(draft_yaml)
        )
        if claimed is None:
            # Not pending / not this request / past TTL: surface the truth
            # for the drifted-draft case (P1-3) and 404 otherwise.
            refuse_stale_draft_claim(self._job_db, workspace_id, request_id, draft_yaml)
            raise NotFoundError("Publish request not found or already resolved")
        try:
            # The key-match guard is a publish gate (422): replay it here so
            # the confirm action is gate-equivalent to the manual publish
            # button.
            require_draft_workflow_key_match(self._job_db, workspace_id, draft_yaml)
            active_before = active_revision_id(self._job_db, workspace_id)
            valid, errors = publish_workflow_draft(
                self._job_db,
                workspace_id,
                draft_yaml,
                self._settings.executor_runtime.workflows.custom_nodes_enabled,
            )
        except Exception:
            # #204 broad-except audit (#429 四轮 P1): the try block spans the
            # full publish pipeline — its failure modes are NOT enumerable
            # (JobServiceError gates, but also a DB drop surfacing as a bare
            # psycopg/OS error, or a process kill between claim and resolve).
            # Every one of them must roll the row back to pending: a
            # ``confirming`` row left behind is invisible to the pending read,
            # untouchable by cancel/supersede, and the create guard 409s all
            # later requests — a permanent dead end for the workspace. The
            # original exception is re-raised after the rollback (the caller
            # still sees the real failure); the stale-confirming TTL sweep
            # (claimed_at) is the backstop when even the rollback cannot run.
            try:
                self._job_db.resolve_publish_request(request_id, status="pending")
            except Exception:
                # #204 broad-except audit: best-effort rollback of an already-
                # failed confirm. Swallowing the secondary error (logged) is
                # deliberate: raising IT would mask the original publish
                # failure, and there is no caller state left to recover — the
                # stale-confirming sweep is the guaranteed eventual cleanup.
                logger.exception(
                    "confirm rollback to pending failed for publish request %s"
                    " (workspace %s); the stale-confirming sweep will recover it",
                    request_id,
                    workspace_id,
                )
            raise
        if not valid:
            # Publish refused (draft drifted after the agent's request): the
            # request returns to pending — the human can fix the draft and
            # confirm again, or cancel. Never resolve on a failed publish.
            self._job_db.resolve_publish_request(request_id, status="pending")
            raise ConflictError("Publish validation failed: " + "; ".join(errors[:5]))
        active_after = active_revision_id(self._job_db, workspace_id)
        produced_revision = active_after is not None and active_after != active_before
        # Known limitation (#429 二轮复审，不修，注释记录): the before/after
        # double probe can misattribute a revision that a concurrent manual
        # publish created between the two probes — this request would record
        # that foreign revision as its own result_revision_id. The window is
        # the publish call's duration and requires a concurrent human publish
        # of the same workspace; the effect is a mislabeled receipt, not data
        # corruption. Fixing it needs a publish call that returns the revision
        # it created, which is the deferred follow-up.
        resolved = self._job_db.resolve_publish_request(
            request_id,
            status="confirmed",
            # result_revision_id is non-null only when the publish created a
            # NEW revision; a runtime-only in-place save updates the existing
            # revision's config without a new version (#429), and the field
            # must say so instead of echoing the unchanged revision id.
            result_revision_id=active_after if produced_revision else None,
        )
        if resolved is None:
            resolved = self._resolve_lost_race(request_id)
        return iso_payload(resolved)

    def cancel(self, workspace_id: str, request_id: str) -> dict[str, Any]:
        """Human cancel: the request lands ``rejected``; the agent keeps its
        draft and can revise + re-request.

        The claim predicate (#429 三轮 P1-2): cancel only moves ``pending``
        rows. A row being confirmed (``confirming``) is untouchable — its
        publish is in flight and will record its own outcome; the cancel
        404s instead of writing ``rejected`` over a revision that is about
        to be live. The Studio dialog guards this client-side (confirming
        disables the close channels); this is the backend boundary that
        holds for cross-tab calls and direct API use."""
        pending = self._job_db.get_pending_publish_request(workspace_id)
        if pending is None or pending["id"] != request_id:
            raise NotFoundError("Publish request not found or already resolved")
        if is_past_expiry(pending):
            # Record the terminal state (best effort — a racing resolution
            # just means the row is already terminal) and refuse.
            self._job_db.expire_pending_publish_request(workspace_id)
            raise NotFoundError("Publish request not found or already resolved")
        resolved = self._job_db.reject_pending_publish_request(
            workspace_id, request_id, status="rejected"
        )
        if resolved is None:
            # Cancel resolves nothing (no publish effect) — losing the race
            # means someone else resolved it first; report that final state.
            resolved = self._job_db.get_publish_request_current_state(request_id)
        if resolved is None:
            raise NotFoundError("Publish request not found or already resolved")
        return iso_payload(resolved)

    # -- internals ---------------------------------------------------------

    def _resolve_lost_race(self, request_id: str) -> dict[str, Any]:
        """The confirm's publish landed but its resolve matched no
        confirming row (#429): the row was concurrently resolved by someone
        else. The publish effect is real, so the truthful answer is the
        row's final state — NOT a 404 pretending nothing happened."""
        final = self._job_db.get_publish_request_current_state(request_id)
        if final is None:
            # The row vanished outright (deleted workspace cascade): the
            # honest answer for a request that no longer exists.
            raise NotFoundError("Publish request not found or already resolved")
        return final
