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
        """The workspace's live pending request (None when there is none)."""
        request = self._job_db.get_pending_publish_request(workspace_id)
        return _iso_payload(request) if request is not None else None

    def confirm(self, workspace_id: str, request_id: str) -> dict[str, Any]:
        """Human confirm: publish the draft through the manual-publish gates.

        The publish itself is the same ``publish_workflow_draft`` call the
        Studio publish endpoint makes; the request row moves to ``confirmed``
        only after that call succeeded, recording the resulting revision.
        """
        self._claim_pending(workspace_id, request_id)
        draft_yaml = workspace_draft_yaml(self._job_db, workspace_id)
        # The key-match guard is a publish gate (422): replay it here so the
        # confirm action is gate-equivalent to the manual publish button.
        require_draft_workflow_key_match(self._job_db, workspace_id, draft_yaml)
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
        resolved = self._job_db.resolve_publish_request(
            request_id,
            status="confirmed",
            result_revision_id=self._active_revision_id(workspace_id),
        )
        if resolved is None:
            raise NotFoundError("Publish request not found or already resolved")
        return _iso_payload(resolved)

    def cancel(self, workspace_id: str, request_id: str) -> dict[str, Any]:
        """Human cancel: the request lands ``rejected``; the agent keeps its
        draft and can revise + re-request."""
        self._claim_pending(workspace_id, request_id)
        resolved = self._job_db.resolve_publish_request(request_id, status="rejected")
        if resolved is None:
            raise NotFoundError("Publish request not found or already resolved")
        return _iso_payload(resolved)

    # -- internals ---------------------------------------------------------

    def _claim_pending(self, workspace_id: str, request_id: str) -> None:
        pending = self._job_db.get_pending_publish_request(workspace_id)
        if pending is None or pending["id"] != request_id:
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
