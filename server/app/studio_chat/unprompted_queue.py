"""Hold human messages while a Kimi Code unprompted turn runs (#1029).

An unprompted turn (#938: background-subagent completion / cron fire) never
claims the session — the row stays ``idle`` — so a message sent meanwhile used
to go straight to ``session/prompt``. Kimi Code 0.43 does not reject it: its
prompt channel (``AgentPromptChannel.submit``) queues the input FIFO behind the
running turn and returns no launch, so the ACP adapter settles the prompt at
once with a zero-content ``end_turn``; the queued input later runs as an
``origin: user`` turn with no ACP driver (its events are dropped) that the
wire watcher skips as Studio-driven. The reply is lost from Studio and the
empty-turn 「继续对话」 would hand the engine the same text twice.

So, while the wire shows an unprompted turn open (``turn.prompt`` seen, no
``turn.ended``), human input is persisted as queued (``content.queued``, the
#1028 row) and held here instead of being sent. When the watcher has durably
written that turn's end, held messages go, in order, to the ACP prompt queue
with #1028's ``before_start`` delivery (claim + ``queued_delivered``, or a
visible ``queued_dropped``). While anything is held, later sends hold too
(FIFO) and background wakeups stand back.

The open state is the watcher projector's, adopted only once every projected
row is persisted, under ``runtime.lock`` — the lock admission takes — so a
send can never observe a turn as ended before its rows exist. Admission steps
the watcher once first (``refresh``) to shrink the poll gap; the residual
window (engine started, journal not yet written) is inherent and only falls
back to the pre-#1029 behavior. A turn that never ends — journal replaced or
truncated (re-baselined, its ``turn.ended`` unobservable), or no journal
progress for ``IDLE_TIMEOUT_SECONDS`` — stops gating, and held messages are
dropped with a visible notice (#1028's undelivered semantics). A runtime torn
down while holding never starts them; the UI marks such rows undelivered.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.studio_chat.inbound_queue import DROPPED_ERROR, _deliver, _note_dropped
from server.app.studio_chat.kimi_wire import kimi_code_homes, locate_wire
from server.app.studio_chat.unprompted_turns import UnpromptedTurnWatcher

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

IDLE_TIMEOUT_SECONDS = 900.0
DROPPED_STALLED = "agent 自发回合长时间无进展，排队消息未投递，请重发"
_LIVE_STATUSES = ("idle", "running", "awaiting_permission")


def holding(runtime: SessionRuntime) -> bool:
    """Caller holds runtime.lock: an unprompted turn is open or messages wait."""
    gate = runtime.unprompted_gate
    return gate is not None and bool(gate.open or gate.held)


def should_hold(runtime: SessionRuntime, status: str) -> bool:
    return status in _LIVE_STATUSES and holding(runtime)


def refresh(runtime: SessionRuntime) -> None:
    """Caller does NOT hold runtime.lock (step lock → runtime.lock order)."""
    gate = runtime.unprompted_gate
    if gate is None:
        return
    try:
        gate.step()
    except Exception:
        # #204 broad-except audit: an admission-time refresh only narrows the
        # poll gap; journal/DB failures are retried by the watcher thread and
        # admission proceeds on the last settled state. Traceback retained.
        logger.warning("unprompted-turn refresh failed for %s", gate.session_id, exc_info=True)


def hold(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    text: str,
    prompt: str,
    commit_wakeup: Callable[[], None],
) -> dict[str, Any] | None:
    """Caller holds runtime.lock with all prompt preparation done (the
    inbound_queue.enqueue contract); None when the runtime is gone."""
    gate = runtime.unprompted_gate
    if gate is None or runtime.closed:
        return None
    message = service.db.enqueue_studio_chat_message(session_id, hash_token(runtime.token), text)
    runtime.inbound_pending += 1
    runtime.resume_transcript_pending = False
    commit_wakeup()
    gate.held.append((str(message["id"]), text, prompt))
    return message


class GatedUnpromptedWatcher(UnpromptedTurnWatcher):
    """The #938 watcher plus the inbound gate it drives after every step."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        # Serializes steps (watcher thread + admission refresh); taken before
        # runtime.lock, never inside it.
        self.step_lock = threading.Lock()
        # Guarded by runtime.lock: open unprompted turn ids, and held
        # (message_id, text, prompt) in arrival order.
        self.open: frozenset[str] = frozenset()
        self.held: list[tuple[str, str, str]] = []
        self.expired: set[str] = set()
        self.activity = time.monotonic()
        self.runtime.unprompted_gate = self

    def _position(self) -> tuple[Any, int] | None:
        return None if self.tail is None else (self.tail.identity, self.tail.offset)

    def step(self) -> None:
        with self.step_lock:
            before = self._position()
            try:
                super().step()
            finally:
                # Settle even when the step failed: a journal that keeps
                # failing must still time out instead of holding forever.
                after = self._position()
                broken = (
                    before is not None
                    and after is not None
                    and before[0] is not None
                    and (after[0] != before[0] or after[1] < before[1])
                )
                if after != before:
                    self.activity = time.monotonic()
                self._settle(broken)

    def _settle(self, broken: bool) -> None:
        runtime = self.runtime
        with runtime.lock:
            if runtime.closed or self.service.runtime(self.session_id) is not runtime:
                return
            turns = self.projector.turns
            if not self.pending:
                # Adopt the projector's view only once its rows are durable.
                self.expired &= turns
                self.open = frozenset(turns) - self.expired
            stalled = time.monotonic() - self.activity >= IDLE_TIMEOUT_SECONDS
            if self.open and (broken or stalled):
                self.expired |= self.open
                self.open = frozenset()
                self._drop_held(DROPPED_STALLED)
            elif not self.open and self.held:
                self._flush()

    def _drop_held(self, detail: str) -> None:
        held, self.held = self.held, []
        for message_id, _text, _prompt in held:
            self.runtime.inbound_pending -= 1
            _note_dropped(self.service, self.session_id, message_id, detail)

    def _flush(self) -> None:
        """Caller holds runtime.lock: hand held messages to the ACP queue in
        order; #1028's ``before_start`` claims each at its turn."""
        service, session_id, runtime = self.service, self.session_id, self.runtime
        held, self.held = self.held, []
        for message_id, text, prompt in held:

            def before_start(m: str = message_id, t: str = text, p: str = prompt) -> bool:
                return _deliver(service, session_id, runtime, m, t, p)

            if not runtime.handle.send_prompt(prompt, before_start=before_start):
                runtime.inbound_pending -= 1
                _note_dropped(service, session_id, message_id, DROPPED_ERROR)


def gated_watcher(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, acp_session_id: str
) -> GatedUnpromptedWatcher:
    """The #938 journal watcher for one ACP session, with the #1029 gate."""
    homes = kimi_code_homes(runtime.handle.cwd)
    return GatedUnpromptedWatcher(
        service, session_id, runtime, lambda: locate_wire(homes, acp_session_id)
    )
