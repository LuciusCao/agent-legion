"""Public admission/cancel helpers backed by the single TurnLease protocol."""

from typing import Any

from server.app.studio_chat.turn_claim import claim_turn_lease
from server.app.studio_chat.turn_lease import (
    TurnLease,
    activate_turn_lease,
    begin_turn_admission,
    cancel_handle,
    cancel_requested,
    cancel_turn_admission,
    disarm_turn_watchdog,
    note_prompt_response,
    request_user_cancel,
    transport_failed,
)

__all__ = [
    "activate_turn_lease",
    "begin_turn_admission",
    "claim_turn_lease",
    "cancel_handle",
    "cancel_requested",
    "cancel_turn_admission",
    "disarm_turn_watchdog",
    "note_prompt_response",
    "prompt_fatal",
    "request_user_cancel",
    "transport_failed",
]


def prompt_fatal(handle: Any, lease: TurnLease | None) -> bool:
    return (isinstance(lease, TurnLease) and lease.fatal_for_prompt()) or transport_failed(handle)
