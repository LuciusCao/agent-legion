"""Agent-initiated workflow publish requests (issue #416): service layer.

Two actors, one handshake:

- **Agent** (studio-agent scoped token, via the ``request_workflow_publish``
  MCP tool): validates the workspace's unpublished draft against the FULL
  publish validation set and parks a pending request. It never publishes —
  no code path from the tool surface reaches the revision pipeline.
- **Human** (full session, via the Studio confirm endpoint): the confirm
  action replays the exact ``publish_workflow_draft`` gates the Studio
  publish button uses (key-match guard included) against the workspace's
  draft-store YAML, then records the resulting revision on the request row.
  A draft that drifted (edited or invalidated after the agent's request)
  fails the same gates and raises — the request stays pending so the human
  can fix the draft and confirm again, or cancel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.studio_agent_tools import studio_agent_created_by
from server.app.services.workflow_draft_key import require_draft_workflow_key_match
from server.app.services.workflow_draft_publish import (
    publish_workflow_draft,
    validate_workflow_draft_for_publish,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.settings import Settings


def workspace_draft_yaml(job_db: JobQueries, workspace_id: str) -> str:
    """The workspace's unpublished draft YAML (schema v61 draft store).

    This is the same YAML the Studio canvas edits and the review dialog's
    compare runs against — confirm publishes what the human reviewed.
    """
    draft = job_db.get_workspace_workflow_draft(workspace_id)
    if draft is None:
        raise ConflictError("No unpublished workflow draft to publish")
    return str(draft["definition_yaml"])


def _iso_payload(request: dict[str, Any]) -> dict[str, Any]:
    """The wire payload: timestamps as ISO strings (datetimes otherwise leak
    Postgres-specific formatting into the MCP tool text)."""
    payload = dict(request)
    for field in ("created_at", "expires_at", "resolved_at"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            payload[field] = value.isoformat()
    return payload


def _is_past_expiry(request: dict[str, Any]) -> bool:
    """Whether a pending row is past its ``expires_at``. The driver hands
    timestamptz back as an ISO string in this deployment, so parse (the
    string is always self-produced UTC ISO format; ``fromisoformat``
    round-trips it)."""
    expires_at = request.get("expires_at")
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    return expires_at <= datetime.now(UTC)


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
        validate_workflow). Never produces a revision.
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
        # Attribution mirrors the draft tools: the run's initiating user
        # behind the studio-agent prefix (STUDIO-AGENT-001 §0.4).
        request = self._job_db.create_pending_publish_request(
            workspace_id,
            studio_agent_created_by(str(user["id"])),
            chat_session_id,
        )
        return _iso_payload(request)

    def get_request_status(self, request_id: str, user: dict[str, Any]) -> dict[str, Any]:
        """One request by id for the agent; authorization mirrors
        build_session_context (bound token → workspace match, unbound →
        workspace membership; mismatches 404 so foreign ids cannot probe)."""
        request = self._job_db.get_publish_request(request_id)
        if request is None or not self._may_read(request, user):
            raise NotFoundError("Publish request not found")
        return _iso_payload(request)

    # -- human side (Studio endpoints) ------------------------------------

    def get_pending(self, workspace_id: str) -> dict[str, Any] | None:
        """The workspace's live pending request (None when there is none).

        Read first, sweep only on observed expiry (#429): the pure read
        returns the pending row regardless of TTL; when that row is past its
        ``expires_at`` one write records the terminal ``expired`` state (so
        the agent's status tool and any later confirm see a terminal row,
        not a zombie pending) and the poll answers None — the dialog's
        "request is gone, close" signal. A healthy workspace polls with one
        read connection and this path itself opens zero write connections
        (the auth session's sliding expiry still writes — that is outside
        this path, not a claim about the request store); the old design
        opened a write on every 5s poll.
        """
        request = self._job_db.get_pending_publish_request(workspace_id)
        if request is None:
            return None
        if _is_past_expiry(request):
            # Best effort: a racing resolution just means the row is already
            # terminal. Either way the poll's answer is "no pending request".
            self._job_db.expire_pending_publish_request(workspace_id)
            return None
        return _iso_payload(request)

    def confirm(self, workspace_id: str, request_id: str) -> dict[str, Any]:
        """Human confirm: publish the draft through the manual-publish gates.

        The publish itself is the same ``publish_workflow_draft`` call the
        Studio publish endpoint makes; the request row moves to ``confirmed``
        only after that call succeeded, recording the resulting revision.

        TOCTOU hardening (#429): the resolve step no longer checks
        ``expires_at``. The claim already checked the TTL, and the publish
        between them may legitimately outlive the remaining TTL — a revision
        that landed must be recorded as confirmed, never denied by a TTL
        that expired mid-publish. The ``status='pending'`` predicate alone
        still guards double-resolution: a confirm whose publish landed but
        whose row was superseded meanwhile resolves it anyway (the publish
        effect is real, and the superseding request reads as its own
        pending/terminal state) — see ``_resolve_or_read_final``.
        """
        self._claim_pending(workspace_id, request_id)
        draft_yaml = workspace_draft_yaml(self._job_db, workspace_id)
        # The key-match guard is a publish gate (422): replay it here so the
        # confirm action is gate-equivalent to the manual publish button.
        require_draft_workflow_key_match(self._job_db, workspace_id, draft_yaml)
        active_before = self._active_revision_id(workspace_id)
        valid, errors = publish_workflow_draft(
            self._job_db,
            workspace_id,
            draft_yaml,
            self._settings.executor_runtime.workflows.custom_nodes_enabled,
        )
        if not valid:
            # Publish refused (draft drifted after the agent's request): the
            # request STAYS pending — the human can fix the draft and confirm
            # again, or cancel. Never resolve on a failed publish.
            raise ConflictError("Publish validation failed: " + "; ".join(errors[:5]))
        active_after = self._active_revision_id(workspace_id)
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
        return _iso_payload(resolved)

    def cancel(self, workspace_id: str, request_id: str) -> dict[str, Any]:
        """Human cancel: the request lands ``rejected``; the agent keeps its
        draft and can revise + re-request."""
        self._claim_pending(workspace_id, request_id)
        resolved = self._job_db.resolve_publish_request(request_id, status="rejected")
        if resolved is None:
            # Cancel resolves nothing (no publish effect) — losing the race
            # means someone else resolved it first; report that final state.
            resolved = self._job_db.get_publish_request_current_state(request_id)
        if resolved is None:
            raise NotFoundError("Publish request not found or already resolved")
        return _iso_payload(resolved)

    # -- internals ---------------------------------------------------------

    def _resolve_lost_race(self, request_id: str) -> dict[str, Any]:
        """The confirm's publish landed but its resolve matched no pending
        row (#429): the row was concurrently resolved by someone else (e.g.
        superseded by a newer agent request mid-publish). The publish effect
        is real, so the truthful answer is the row's final state — which a
        concurrent supersede leaves as ``superseded`` (the newer request
        owns the pending slot) — NOT a 404 pretending nothing happened."""
        final = self._job_db.get_publish_request_current_state(request_id)
        if final is None:
            # The row vanished outright (deleted workspace cascade): the
            # honest answer for a request that no longer exists.
            raise NotFoundError("Publish request not found or already resolved")
        return final

    def _claim_pending(self, workspace_id: str, request_id: str) -> None:
        """The pre-action gate: the named request must be the workspace's
        pending row AND still within its TTL. This is the ONLY TTL check on
        the confirm/cancel path (#429): the later resolve intentionally
        drops the expiry predicate so a publish that outlives the remaining
        TTL still records its effect."""
        pending = self._job_db.get_pending_publish_request(workspace_id)
        if pending is None or pending["id"] != request_id:
            raise NotFoundError("Publish request not found or already resolved")
        if _is_past_expiry(pending):
            # Record the terminal state (best effort — a racing resolution
            # just means the row is already terminal) and refuse.
            self._job_db.expire_pending_publish_request(workspace_id)
            raise NotFoundError("Publish request not found or already resolved")

    def _may_read(self, request: dict[str, Any], user: dict[str, Any]) -> bool:
        bound = user.get("scoped_workspace_id")
        if bound is not None:
            return bool(request["workspace_id"] == bound)
        if user.get("role") == "admin":
            return True
        return (
            self._job_db.get_workspace_role(str(request["workspace_id"]), str(user["id"]))
            is not None
        )

    def _active_revision_id(self, workspace_id: str) -> str | None:
        workspace = self._job_db.get_workspace(workspace_id)
        if workspace is None:
            return None
        workflow_key = str(workspace.get("default_workflow_key") or "")
        revision = self._job_db.get_active_workflow_revision(workspace_id, workflow_key)
        return str(revision["id"]) if revision is not None else None
