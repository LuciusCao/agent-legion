"""Live (in-memory) per-session runtime state containers (studio chat).

Split from service.py (file budget): the runtime containers; the single
teardown path (deny pending permissions, stop the subprocess, revoke the
scoped token) lives in teardown.py.
"""

from __future__ import annotations

import threading
from typing import Any

from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.streaming import TurnStreamState


class PendingPermission:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.event = threading.Event()
        self.decision: dict[str, Any] = {"deny": True}


class SessionRuntime:
    """Live (in-memory) state for one chat session; never persisted."""

    def __init__(self, handle: AcpSessionHandle, token: str) -> None:
        self.handle = handle
        # Raw scoped token, held only to revoke on close; never leaves memory.
        self.token = token
        # RLock, not Lock: send_message's turn-start critical section (#694
        # review R2-P1) calls helpers that take the same lock on the same
        # thread (prompt preparation and credential checks).
        # Cross-thread exclusion semantics are unchanged.
        self.lock = threading.RLock()
        self.pending_permissions: dict[str, PendingPermission] = {}
        # Set under lock by teardown before the pending-permission settle
        # sweep: a permission request that parks afterwards (it takes the same
        # lock) denies immediately instead of hanging to the timeout (#158).
        self.closed = False
        self.background_stop = threading.Event()
        self.background_wakeup_enabled = True
        self.background_epoch = 0
        self.background_rearm_epoch: int | None = None
        (self.background_baseline, self.background_cursor) = (None, None)  # type: (Any, Any)
        # #938: pre-spawn baseline of the resumed Kimi Code wire journal
        # (wire_baseline.WireBaseline); None for a session this runtime created.
        (self.wire_baseline, self.background_cleanup) = (None, None)  # type: (Any, Any)
        # Streaming chunk coalescing (agent text + thought): each kind folds
        # into one message row per turn; the slots are reset at turn START
        # (send_message), so trailing chunks of a finished turn still fold
        # into that turn's rows (#98).
        self.stream = TurnStreamState()
        # #1120 tool_call coalescing ownership map: toolCallId → the call's
        # single message row id, so tool_call_update frames merge into the
        # first frame's row instead of appending full-snapshot duplicates.
        # In-memory only, guarded by ``lock``: a resume/restart starts empty
        # and an update for a pre-restart toolCallId degrades to a fresh row
        # (behavioural fallback, never data loss). Entries drop at terminal
        # status (completed/failed), and the whole map drops at every turn
        # boundary (#1228 codex P2: a toolCallId is unique within one turn —
        # see tool_call_coalesce.clear_tool_call_ownership).
        self.tool_call_messages: dict[str, str] = {}
        self.mcp_observed = self.mcp_hint_shown = False
        # Set by resume_session when the fresh agent could not reload the
        # prior ACP session: the first post-resume prompt gets the persisted
        # transcript prepended (consumed and cleared in send_message).
        self.resume_transcript_pending = False
        # Whether the one-time advisory mcp_unverified hint was already
        # posted for this session (per-session, not per-turn).
        # Mid-turn token keepalive bookkeeping (#411): set only AFTER the
        # run_token_invalidated notice was successfully appended — later
        # tool_call updates then skip re-emitting it (one warning is enough;
        # a resume builds a fresh runtime with a fresh token and its own
        # flag). Live-token checks and failed notices do NOT set it, so
        # death is always detected on the first tool_call after it happens.
        # Guarded by ``lock`` like every notification-path field.
        self.token_keepalive_done = False
        # Agent config switching (#368, PR #393 review): config_lock
        # serializes the whole validate→forward→write-back span of a switch
        # (held across the RPC — never take it on the notification path);
        # config_version (guarded by ``lock``) bumps on every accepted
        # notification rewrite so an overtaken switch skips its stale
        # write-back.
        self.config_lock = threading.Lock()
        self.config_version = 0
        # #694 context-health signals (compaction.py): loading suppresses
        # session/load replay chunks — armed by spawn_session_runtime only
        # when a session/load is actually attempted (resume with a prior ACP
        # session id), closed by the first post-resume prompt in
        # send_message; compacting mirrors the kimi background-compaction
        # window (send guard + UI), self-clearing after
        # compaction.COMPACTING_TIMEOUT_SECONDS via compacting_since.
        self.loading = self.compacting = False
        self.compacting_since: float | None = None
        # Active self-clear timers for the current compaction window and the
        # deferred empty-turn verdict; both are cancelled on completion/teardown.
        (self.compact_timer, self.empty_turn_timer) = (None, None)  # type: (threading.Timer | None, threading.Timer | None)
        # #694 review R2-P2 marker gate: kimi_agent is stamped at on_ready
        # from the registry agent id / ACP agentInfo name; turn_open spans a
        # prompt turn (send_message → turn close), and turn_may_compact marks
        # a /compact turn (manual compaction emits its markers in-turn).
        self.kimi_agent = self.turn_open = False
        self.turn_owner: object | None = None
        self.turn_may_compact = False
        # compaction_seen: an accepted compaction start marker in this
        # process lifetime — the evidence gate for the empty_turn wording
        # (#863); cleared by on_ready like the flag itself.
        self.compaction_seen = False
        # Coalesces duplicate completion signals across wire and legacy text;
        # reset by a new window, timeout, or fresh process (never an outcome).
        self.compaction_done = False
        # Advances on every accepted open/close; pending wire retries must not
        # replay a signal after another source has advanced the lifecycle.
        self.compaction_generation = 0
        # Per-turn bookkeeping for the degenerate-turn detector
        # (empty_turn.py): open_turn stamps turn_started_at (also the turn's
        # identity for the deferred verdict) / zeroes turn_update_count /
        # exempts slash commands and platform-initiated (empty-text) turns;
        # on_update counts content updates, trailing ones included.
        self.turn_started_at: float | None = None
        self.turn_update_count = 0
        self.turn_skip_empty_check = self.turn_background = False
        # #882 (inbound_queue.py / empty_turn_retry.py): turn_background marks a
        # platform-initiated turn (background wakeup); turn_retry_source is the
        # (message_id, text, prompt) of the human message behind the open turn,
        # promoted to empty_turn_retry when that turn is confirmed empty — the
        # one replay 「继续对话」 may deliver (cleared by any new turn).
        # inbound_pending counts human messages queued behind the turn in
        # flight and not yet started.
        self.turn_retry_source: tuple[str, str, str] | None = None
        self.empty_turn_retry: tuple[str, str, str] | None = None
        self.inbound_pending = 0
        # Live delivery states are message_id -> queued|delivered. Delivered
        # means before_start committed, never that the model produced a result.
        self.inbound_messages: dict[str, str] = {}
        # Pending request/message ids stay until their terminal rows are
        # durable; the runtime identity fences all exit projection retries.
        self.exit_started = self.exit_projection_done = False
        self.exit_event_id: str | None = None
        # #1029: the Kimi Code wire watcher (unprompted_queue.GatedUnprompted-
        # Watcher) that holds human messages while an unprompted turn runs;
        # None for non-Kimi-Code runtimes.
        self.unprompted_gate: Any = None
