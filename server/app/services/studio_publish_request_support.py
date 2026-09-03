"""Shared helpers for the studio publish-request handshake (#429 三轮).

Split from services/studio_publish_requests.py (file budget): the wire-
payload shaping, the lazy-expiry timestamp comparison, and the draft-version
token are used by the service layer but carry no state-machine semantics of
their own.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import ConflictError

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

# #429 四轮 P1: how long a ``confirming`` row may sit before readers treat
# it as a dead process's claim (the confirm died between claim and resolve).
# A healthy publish completes in seconds; 5 minutes absorbs slow disk/
# pagination without ever sweeping a live claim.
CONFIRMING_STALE_SECONDS = 300


def workspace_draft_yaml(job_db: JobQueries, workspace_id: str) -> str:
    """The workspace's unpublished draft YAML (schema v61 draft store) — the
    same YAML the Studio canvas edits and the review dialog's compare runs
    against; confirm publishes what the human reviewed."""
    draft = job_db.get_workspace_workflow_draft(workspace_id)
    if draft is None:
        raise ConflictError("No unpublished workflow draft to publish")
    return str(draft["definition_yaml"])


def refuse_stale_draft_claim(
    job_db: JobQueries, workspace_id: str, request_id: str, draft_yaml: str
) -> None:
    """Why a confirm claim failed, when it matters: the named row is still
    pending but the server draft is no longer the one the agent requested
    (#429 三轮 P1-3) — the confirm must refuse loudly instead of publishing
    a draft the human never reviewed. No-op for every other failure shape
    (missing/terminal/expired rows stay 404)."""
    pending = job_db.get_pending_publish_request(workspace_id)
    if pending is None or pending["id"] != request_id:
        return
    if pending.get("draft_hash") is not None and pending["draft_hash"] != (
        draft_yaml_hash(draft_yaml)
    ):
        raise ConflictError(
            "Draft changed after the publish request was created;"
            " ask the agent to re-request the publish"
        )


def draft_yaml_hash(draft_yaml: str) -> str:
    """The draft-version token (#429 三轮 P1-3): sha256 of the raw draft
    YAML. Recorded when the agent parks its request and re-checked when the
    human confirms, so the confirmed publish is exactly the draft the agent
    asked about — never a newer save that landed in between. Raw-string
    hashing (not ``definition_hash``'s canonical form): the draft store
    persists the literal YAML, and byte-identity of that literal is the
    binding that matters here."""
    return hashlib.sha256(draft_yaml.encode("utf-8")).hexdigest()


def iso_payload(request: dict[str, Any]) -> dict[str, Any]:
    """The wire payload: timestamps as ISO strings (datetimes otherwise leak
    Postgres-specific formatting into the MCP tool text)."""
    payload = dict(request)
    for field in ("created_at", "expires_at", "resolved_at", "claimed_at"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            payload[field] = value.isoformat()
    return payload


def is_past_expiry(request: dict[str, Any]) -> bool:
    """Whether a pending row is past its ``expires_at``. The driver hands
    timestamptz back as an ISO string in this deployment, so parse (always
    self-produced UTC ISO; ``fromisoformat`` round-trips it)."""
    expires_at = request.get("expires_at")
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    return expires_at <= datetime.now(UTC)


def is_stale_claim(request: dict[str, Any]) -> bool:
    """Whether a ``confirming`` row's claim is past the stale threshold
    (#429 四轮 P1): the claiming process is presumed dead. String-tolerant
    like ``is_past_expiry``. A row without ``claimed_at`` reads as NOT
    stale — the safe default never risks expiring a live claim."""
    claimed_at = request.get("claimed_at")
    if claimed_at is None:
        return False
    if isinstance(claimed_at, str):
        claimed_at = datetime.fromisoformat(claimed_at)
    return claimed_at <= datetime.now(UTC) - timedelta(seconds=CONFIRMING_STALE_SECONDS)


def may_read_request(job_db: JobQueries, request: dict[str, Any], user: dict[str, Any]) -> bool:
    """Authorization for the agent's status tool, mirroring
    build_session_context: a workspace-bound token reads its own workspace;
    an unbound token needs workspace membership (admin passes)."""
    bound = user.get("scoped_workspace_id")
    if bound is not None:
        return bool(request["workspace_id"] == bound)
    if user.get("role") == "admin":
        return True
    return job_db.get_workspace_role(str(request["workspace_id"]), str(user["id"])) is not None


def active_revision_id(job_db: JobQueries, workspace_id: str) -> str | None:
    """The workspace's active revision id (None when there is none) — the
    before/after probe the confirm uses to attribute result_revision_id."""
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        return None
    workflow_key = str(workspace.get("default_workflow_key") or "")
    revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
    return str(revision["id"]) if revision is not None else None
