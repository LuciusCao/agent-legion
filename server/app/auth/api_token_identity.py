"""Workspace API token identity resolution + #738 rate limit (HTTP side).

Split from ``dependencies.get_current_user`` (file budget): resolves a
Bearer ``{token_id}.{secret}`` into the api-scope machine identity and
charges the token's request bucket right there — the one chokepoint every
api-token request passes, whatever route it targets. The bucket is charged
only AFTER the secret verified: a caller who merely knows a public token_id
cannot drain someone else's budget, and studio cookie sessions / scoped
tokens never reach this module, so they are never limited.

Refusals are HTTP 429 with ``Retry-After`` and a structured warning keyed by
token_id (the token audit surface is the structured log, same as the #626
submission records). The warning is throttled per token so a storm being
refused does not become a log storm; the suppressed count rides the next
record.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import Request
from fastapi.exceptions import HTTPException

from server.app.auth.api_token_limits import LimitDecision
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE, split_api_token

logger = logging.getLogger(__name__)

_REFUSAL_LOG_INTERVAL_SECONDS = 60.0
_refusal_log_state: dict[str, tuple[float, int]] = {}
_refusal_log_lock = threading.Lock()


def _log_refusal(token_id: str, workspace_id: str, decision: LimitDecision) -> None:
    now = time.monotonic()
    with _refusal_log_lock:
        last, suppressed = _refusal_log_state.get(token_id, (float("-inf"), 0))
        if now - last < _REFUSAL_LOG_INTERVAL_SECONDS:
            _refusal_log_state[token_id] = (last, suppressed + 1)
            return
        _refusal_log_state[token_id] = (now, 0)
    logger.warning(
        "workspace api token rate limited: token_id=%s workspace_id=%s"
        " retry_after=%s suppressed_since_last=%s",
        token_id,
        workspace_id,
        decision.retry_after_seconds,
        suppressed,
    )


def resolve_api_token_identity(request: Request, token: str) -> dict[str, Any] | None:
    """The api-scope identity for a valid token (or None); 429 when the
    token's request bucket is empty."""
    if split_api_token(token) is None:
        return None
    store = request.app.state.workspace_api_token_store
    resolved = store.resolve_api_token(token)
    if resolved is None:
        return None
    token_id, workspace_id = resolved["token_id"], resolved["workspace_id"]
    # One charge per HTTP request even if the dependency chain resolves twice.
    if not getattr(request.state, "api_token_rate_charged", False):
        request.state.api_token_rate_charged = True
        decision = store.limiter.acquire_request(token_id)
        if not decision.allowed:
            _log_refusal(token_id, workspace_id, decision)
            raise HTTPException(
                status_code=429,
                detail="API token rate limit exceeded",
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
    return {
        "actor_scope": WORKSPACE_API_SCOPE,
        "scoped_workspace_id": workspace_id,
        "api_token_id": token_id,
    }
