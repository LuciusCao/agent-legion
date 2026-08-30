"""ACP session establishment: session/new, or session/load on the resume path.

Split from acp_session.py (file budget). session/load is attempted only when
the freshly-initialized agent advertises loadSession; any load failure falls
back to a fresh session, and the service rebuilds context from the persisted
transcript instead.
"""

from __future__ import annotations

import logging
from typing import Any

from acp.exceptions import RequestError
from acp.schema import HttpMcpServer

logger = logging.getLogger(__name__)


async def open_acp_session(
    conn: Any,
    *,
    cwd: str,
    mcp_server: HttpMcpServer,
    resume_acp_session_id: str | None,
    capabilities: dict[str, Any],
) -> tuple[str, bool]:
    """Open the ACP session; returns (acp_session_id, loaded_existing).

    ``capabilities`` must come from THIS process's initialize response — the
    persisted snapshot of a previous process says nothing about the fresh one.
    """
    if resume_acp_session_id and capabilities.get("loadSession"):
        try:
            await conn.load_session(
                cwd=cwd,
                session_id=resume_acp_session_id,
                mcp_servers=[mcp_server],
            )
            return resume_acp_session_id, True
        except RequestError as exc:
            # The agent kept the session on its own side (e.g. kimi's local
            # session store) and may have lost it: fall back to a fresh ACP
            # session, never fail the resume.
            # #204 broad-except audit: load_session is a single JSON-RPC
            # request through the acp SDK — its agent-side refusal surfaces
            # as RequestError (the SDK wraps every JSON-RPC error object and
            # transport/timeout failure into that family). A programming
            # error in OUR call path no longer degrades into "fall back to
            # session/new": it now fails the resume loudly, because silently
            # re-opening a fresh session would drop the resumed context
            # without any record that the load path is broken.
            logger.warning(
                "studio chat session/load of %s failed, falling back to session/new: %s",
                resume_acp_session_id,
                exc,
            )
    session = await conn.new_session(cwd=cwd, mcp_servers=[mcp_server])
    return str(session.session_id), False
