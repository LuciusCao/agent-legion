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

from acp.schema import TextContentBlock

logger = logging.getLogger(__name__)

# Safety net for a wedged agent turn; handle.cancel() is the intended
# control path.
PROMPT_TIMEOUT_SECONDS = 3600
# Grace for the agent to wind the turn down after session/cancel; a prompt
# task still pending past it means the cancel never landed (wedged stdio).
CANCEL_GRACE_SECONDS = 30


class PromptWedgedError(RuntimeError):
    """The turn outlived the timeout AND the post-cancel grace: the agent
    never acknowledged session/cancel, so the transport is wedged and the
    session cannot continue — fatal, handled by the session's error path."""


def _log_orphan_result(task: asyncio.Task[Any]) -> None:
    """Retrieve an orphaned prompt task's result so a failure never surfaces
    as an unretrieved-exception warning while the session loop tears down."""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.warning("studio chat ACP orphaned prompt task failed: %s", exc)


def _orphan(task: asyncio.Task[Any]) -> None:
    """Abandon a wedged prompt task: cancel it locally (the agent side is
    already lost) and consume its eventual result via the done callback."""
    task.cancel()
    task.add_done_callback(_log_orphan_result)


async def run_prompt_turn(
    conn: Any, acp_session_id: str, text: str, *, on_timeout: Callable[[], None]
) -> Any:
    """One prompt turn: prompt → on timeout settle+cancel → grace → wedged.

    The prompt runs as a task and asyncio.wait never cancels it on timeout,
    so the timeout path can still deliver the cancel and harvest a late
    response. ``on_timeout`` fires before the cancel is sent — the service
    uses it to settle parked permissions as denied, because an agent parked
    on session/request_permission can only end its prompt after the reply
    arrives; skipping it would misread a healthy session as wedged once the
    grace expires (#664 review). Raises PromptWedgedError when the cancel
    cannot be sent or the grace expires; any task exception (agent refusal
    etc.) propagates as-is for the caller's per-turn containment.
    """
    prompt_task = asyncio.create_task(
        conn.prompt(acp_session_id, [TextContentBlock(type="text", text=text)])
    )
    done, _pending = await asyncio.wait({prompt_task}, timeout=PROMPT_TIMEOUT_SECONDS)
    if not done:
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
        # Loop-local cancel path — unlike the cross-thread handle.cancel(),
        # conn is directly usable here.
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
        done, _pending = await asyncio.wait({prompt_task}, timeout=CANCEL_GRACE_SECONDS)
        if not done:
            _orphan(prompt_task)
            raise PromptWedgedError(
                f"prompt turn wedged: no response within {CANCEL_GRACE_SECONDS}s "
                "after session/cancel"
            )
    # Agent honoured the cancel (usually stop_reason="cancelled"), or the
    # turn finished in time; a task exception surfaces at result() and falls
    # into the caller's per-turn containment like any other turn failure.
    return prompt_task.result()
