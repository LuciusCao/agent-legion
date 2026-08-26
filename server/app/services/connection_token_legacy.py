"""Legacy frozen-node hooks for connection token invalidation.

Kept apart from :mod:`connection_tokens` (budget + cohesion): the token
service is the hot read path, while these hooks exist only for legacy frozen
node code that still calls ``report_node_auth_failure`` in runtime payloads.
Current nodes report auth failures through the marker channel
(``NodeContext.report_auth_failure`` in ``workspace_libs.node_sdk``) — the
parent executor performs the invalidation after the child exits.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from server.app.services.connection_tokens import ConnectionTokenService

logger = logging.getLogger(__name__)


def report_node_auth_failure(runtime: Mapping[str, Any]) -> None:
    """Legacy in-runtime hook: invalidate the connection's cached token.

    Superseded by the marker channel (``NodeContext.report_auth_failure`` in
    ``workspace_libs.node_sdk``): current nodes hold no database handle, so
    the parent executor performs the invalidation after the child exits. This
    function stays for legacy frozen node code that still calls it; it is a
    silent no-op when the runtime carries no connection or no DB handle.
    """
    node_config = runtime.get("node_config")
    key = (
        str(node_config.get("connection") or "").strip() if isinstance(node_config, Mapping) else ""
    )
    # The code executors pop ``_job_db_path`` before invoking node code and
    # hand the node a JobQueries handle instead (``runtime["job_db"]``, see
    # server/app/executors/code.py and _code_sandbox.py): resolve the DSN from
    # that handle first and keep ``_job_db_path`` only as a fallback for
    # runtimes that still carry the raw path.
    job_db = runtime.get("job_db")
    job_db_path = getattr(job_db, "path", None)
    dsn = str(job_db_path).strip() if job_db_path else ""
    if not dsn:
        dsn = str(runtime.get("_job_db_path") or "").strip()
    if not key or not dsn:
        return
    try:
        ConnectionTokenService(dsn).report_auth_failure(key)
    except Exception:  # reporting must never mask the original failure
        logger.exception("connection %s: failed to report auth failure", key)
