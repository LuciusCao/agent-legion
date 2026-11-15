"""Prompt-turn timeout ladder for studio chat ACP sessions (#664).

A locally-timed-out ``session/prompt`` used to be abandoned client-side
only: the agent-side turn kept running and rejected every later prompt
("another turn is already in progress"), leaving the session a zombie at
idle with no recovery entry (resume only accepts closed/error rows). The
ladder here sends ``session/cancel`` on timeout, grants a short grace for
the agent to wind the turn down, and only then declares the turn wedged —
a fatal condition the caller routes into the session error path so resume
becomes reachable and the subprocess is torn down.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from server.app.studio_chat.process_exit import AcpConnectionLost
from server.app.studio_chat.prompt_outcome import PromptTurnResult, PromptWedgedError, _orphan
from server.app.studio_chat.prompt_start import PromptStartGate, acp_prompt, run_prompt_start

logger = logging.getLogger(__name__)

# Safety net for a wedged agent turn; handle.cancel() is the intended
# control path.
PROMPT_TIMEOUT_SECONDS = 3600
# Grace for the agent to wind the turn down after session/cancel; a prompt
# task still pending past it means the cancel never landed (wedged stdio).
CANCEL_GRACE_SECONDS = 30


async def run_prompt_turn(
    conn: Any,
    acp_session_id: str,
    text: str,
    *,
    on_timeout: Callable[[], None],
    before_start: Callable[[], bool] | None = None,
    death_task: asyncio.Task[str | None] | None = None,
    timeout_ladder: tuple[float, float] | None = None,
    start_gate: PromptStartGate | None = None,
) -> PromptTurnResult:
    """One prompt turn, with connection death ahead of result and timeout."""
    total, grace = timeout_ladder or (PROMPT_TIMEOUT_SECONDS, CANCEL_GRACE_SECONDS)

    async def start() -> Any:
        # The dequeue guard takes runtime.lock and may step the Kimi wire
        # watcher (journal read + DB writes, #1109): run it on a worker
        # thread, never on the ACP event loop.
        if before_start is not None and not await asyncio.to_thread(before_start):
            return None
        return await run_prompt_start(start_gate, acp_prompt(conn, acp_session_id, text))

    def death_result(timed_out: bool = False) -> PromptTurnResult:
        assert death_task is not None
        _orphan(prompt_task)
        detail = death_task.result()
        if detail is None:
            return PromptTurnResult(response=None, timed_out=timed_out)
        raise AcpConnectionLost(detail)

    prompt_task = asyncio.create_task(start())
    waiters = {prompt_task} | ({death_task} if death_task is not None else set())
    done, _pending = await asyncio.wait(waiters, timeout=total, return_when=asyncio.FIRST_COMPLETED)
    if death_task in done:
        return death_result()
    timed_out = prompt_task not in done
    if timed_out:
        try:
            on_timeout()
        except Exception:
            # #204 broad-except audit: the settle hook is best-effort
            # preparation for the cancel — its failure must never skip the
            # session/cancel below, because a skipped cancel is exactly the
            # zombie-session failure this ladder exists to fix (#664). The
            # hook failure is logged with its traceback; the cancel still
            # goes out and the grace verdict decides the outcome.
            logger.warning("studio chat turn-timeout hook failed", exc_info=True)
        try:
            await conn.cancel(acp_session_id)
        except Exception as exc:
            # #204 broad-except audit: a failed cancel send is itself proof
            # the transport is dead (RequestError from a half-closed
            # connection, OSError from the stdio pipe), and the outcome is
            # identical to the grace expiring — the turn is unreachable, so
            # it joins the wedged escalation with the cause chained instead
            # of being retried or dropped into per-turn containment.
            _orphan(prompt_task)
            raise PromptWedgedError(f"prompt timed out and session/cancel failed: {exc}") from exc
        done, _ = await asyncio.wait(waiters, timeout=grace, return_when=asyncio.FIRST_COMPLETED)
        if death_task in done:
            return death_result(timed_out=True)
        if prompt_task not in done:
            _orphan(prompt_task)
            raise PromptWedgedError(
                f"prompt turn wedged: no response within {grace}s after session/cancel"
            )
    return PromptTurnResult(response=prompt_task.result(), timed_out=timed_out)
