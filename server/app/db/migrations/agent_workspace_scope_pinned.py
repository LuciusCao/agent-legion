"""Warning side-channel of the v46 agent workspace scope migration.

Queued agent requests pinned to a version other than the v1 copy the
migration creates (or to an archived version) can never match the claim
scan afterwards: they fail closed and are swept to failure by
fail_stale_definition_requests. Declared per issue #101 — the direction is
intentional, the cost is surfaced here.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PINNED_VERSIONS_LEFT_BEHIND = (
    "select count(*) as n from agent_execution_requests "
    "where state = 'queued' and pinned_agent_version > 1"
)


def warn_pinned_versions_left_behind(conn: Any) -> None:
    pinned = int(conn.execute(_PINNED_VERSIONS_LEFT_BEHIND).fetchone()["n"])
    if pinned:
        logger.warning(
            "agent workspace scope migration: %d queued agent request(s) pin an Agent "
            "version other than the v1 workspace copy; they can never match the claim "
            "scan and will be swept to failure (fail-closed, intentional, #101)",
            pinned,
        )
