"""Which workspaces an authenticated identity may see (#711 / #881).

One decision shared by every surface that enumerates workspaces — the
``GET /api/workspaces`` listing (#711), the dashboard SSE stats stream
(#881) and the Agent Worker listing (#752) — so they can never disagree
about who sees what. The membership read is injected (the JobQueries
facade's ``list_user_workspace_ids``); this module owns the mapping from the
request identity onto its two narrowing inputs and how they combine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE


def workspace_visibility_scope(user: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(member_user_id, bound_workspace_id)`` for ``user``.

    - admin full session: ``(None, None)`` — unrestricted;
    - non-admin: restricted to its member workspaces (any role);
    - studio-agent scoped token: inherits its minter's identity, and a
      workspace-bound one is further narrowed to its binding (a binding
      never widens visibility — ``enforce_scoped_workspace_binding``'s
      read-side rule);
    - workspace API token (#626): a machine identity with no member row,
      narrowed to exactly its bound workspace (an empty binding sees
      nothing). The membership guards already 404 it on scopeless
      surfaces; this arm is defense in depth.
    """
    bound = user.get("scoped_workspace_id")
    if user.get("actor_scope") == WORKSPACE_API_SCOPE:
        return None, str(bound or "")
    member_user_id = None if user.get("role") == "admin" else str(user["id"])
    return member_user_id, (str(bound) if bound else None)


def narrow_visible_workspace_ids(
    member_user_id: str | None,
    bound_workspace_id: str | None,
    list_member_workspace_ids: Callable[[str], Iterable[str]],
) -> frozenset[str] | None:
    """Apply the two narrowing inputs; None = unrestricted.

    Only a membership restriction calls ``list_member_workspace_ids`` (one
    member-row read); a binding is applied on top and never widens.
    """
    bound = None if bound_workspace_id is None else frozenset({bound_workspace_id})
    if member_user_id is None:
        return bound
    visible = frozenset(list_member_workspace_ids(member_user_id))
    return visible if bound is None else visible & bound


def visible_workspace_ids_for(
    user: dict[str, Any], list_member_workspace_ids: Callable[[str], Iterable[str]]
) -> frozenset[str] | None:
    """The workspace ids ``user`` may see (None = unrestricted, i.e. admin)."""
    return narrow_visible_workspace_ids(
        *workspace_visibility_scope(user), list_member_workspace_ids
    )
