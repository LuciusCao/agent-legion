"""Membership narrowing for ``GET /api/agent-workers`` (#752).

The listing is a ``require_user`` surface read by every member's UI (the
monitoring panel, the workspace settings worker list, worker-readiness
hints), so it is narrowed rather than closed to admins. Visibility follows
the workspace listing rule (``auth/workspace_visibility``):

- unrestricted identities (admin full session) keep the full view;
- a restricted identity only sees workers whose admission scope intersects
  its visible workspaces, and each row's ``allowed_workspaces`` is cut down
  to that intersection — other workspaces' ids never leave the server;
- legacy ``[]`` scope rows (pre-scoped-token registrations) stay admin-only,
  the same rule the per-workspace view already applies;
- ``register_token_ids`` (the worker↔key binding the admin key manager
  renders) is emptied: the binding spans workspaces and the member views
  never read it;
- a ``workspace_id`` narrowing outside the visible set yields an empty list
  (not a 403/404), so a workspace's existence is not probeable here either.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import Depends, Request

from server.app.auth.dependencies import require_user
from server.app.auth.workspace_visibility import visible_workspace_ids_for


def visible_worker_scope(
    request: Request, user: Annotated[dict[str, Any], Depends(require_user)]
) -> frozenset[str] | None:
    """The caller's visible workspace ids (None = unrestricted admin view)."""
    return visible_workspace_ids_for(user, request.app.state.job_db.list_user_workspace_ids)


# Route parameter type: resolves (and authenticates) the caller's visibility.
VisibleWorkspaces = Annotated[frozenset[str] | None, Depends(visible_worker_scope)]


def narrow_workers_to_visible(
    workers: Iterable[dict[str, Any]],
    visible: frozenset[str] | None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Filter and trim worker payloads for a caller seeing ``visible``.

    ``visible`` is None for unrestricted callers (rows returned as-is).
    """
    if visible is None:
        return list(workers)
    if workspace_id is not None and workspace_id not in visible:
        return []
    narrowed: list[dict[str, Any]] = []
    for worker in workers:
        scope = [ws for ws in worker.get("allowed_workspaces") or [] if ws in visible]
        if not scope:
            continue
        narrowed.append({**worker, "allowed_workspaces": scope, "register_token_ids": []})
    return narrowed
