"""Studio authoring surface guard (P4).

Full sessions must be admin; scoped tokens pass through and keep their
STUDIO-AGENT-001 semantics (draft/validate reachable, effecting endpoints
still refused downstream by ``reject_studio_agent_scope``). New scoped tokens
are admin-origin by construction: minting self-service tokens
(/api/studio-agent-tokens, require_admin) and opening studio chat sessions
(admin-only Studio surface) are both closed to non-admin users.
"""

from typing import Annotated, Any

from fastapi import Depends
from fastapi.exceptions import HTTPException

from server.app.auth.dependencies import get_current_user


def require_studio_authoring(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Full sessions must be admin; scoped tokens keep STUDIO-AGENT-001."""
    if user.get("actor_scope"):
        return user
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
