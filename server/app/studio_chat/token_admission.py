"""Generation-bound, fail-closed credential check for human prompt admission."""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.auth.sessions import hash_token
from server.app.services.job_errors import ConflictError
from server.app.studio_chat.token_keepalive import TOKEN_INVALIDATED_DETAIL, invalidate_run_token

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime


def require_live_run_token(
    backend: ServiceBackend, session_id: str, runtime: SessionRuntime
) -> None:
    """Fail closed before admission; stale generations never escalate successors."""
    with runtime.lock:
        if runtime.closed or backend.runtime(session_id) is not runtime:
            raise ConflictError("Chat session is not running on this server")
        alive = backend.db.get_scoped_token_user(hash_token(runtime.token)) is not None
        if runtime.closed or backend.runtime(session_id) is not runtime:
            raise ConflictError("Chat session is not running on this server")
        if alive:
            return
        invalidate_run_token(backend, session_id, runtime)
    raise ConflictError(TOKEN_INVALIDATED_DETAIL)
