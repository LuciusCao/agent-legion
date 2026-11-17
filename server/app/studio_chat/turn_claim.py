"""DB claim outcome handling for the single TurnLease owner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.turn_lease import TurnLease

logger = logging.getLogger(__name__)


def claim_turn_lease(
    runtime: SessionRuntime, claim: Any, *, claim_status: Any = None
) -> TurnLease | None:
    from server.app.studio_chat.turn_lease import begin_turn_admission

    lease = begin_turn_admission(runtime)
    if lease is None:
        return None
    try:
        claimed = claim()
    except Exception as exc:
        # #204 broad-except audit: only positive/negative durable proof may
        # recover or abort; an unknown commit retains the fatal owner and re-raises.
        try:
            status = claim_status() if claim_status is not None else None
        except Exception:
            # #204 broad-except audit: a failed proof read is itself uncertain;
            # preserve the original claim exception and choose conservative fatal.
            logger.warning("studio lease claim proof read failed", exc_info=True)
            status = None
        if status is False:
            lease.abort()
        elif status is True:
            return lease
        else:
            lease.fail_claim_uncertain(f"{type(exc).__name__}: {exc}")
        raise
    if claimed:
        return lease
    lease.abort()
    return None
