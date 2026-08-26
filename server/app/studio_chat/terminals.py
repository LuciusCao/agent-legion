"""ACP terminal backing for Studio chat sessions (terminal/create & friends).

kimi ≥ 0.38 runs its Bash/Grep tools through the ACP terminal protocol: the
agent asks the *client* to spawn ``shell -c <command>`` and polls the output
via ``terminal/output``. Until the backend implemented the five
``terminal/*`` methods, every such tool call failed with "ACP terminal
capability is unavailable" — the agent never even asked for permission.

Security model: the agent-side Bash tool is permission-gated by
``session/request_permission`` BEFORE ``terminal/create`` reaches us (kimi
requests approval, the human answers, only then does it spawn). The methods
here therefore only run commands the human already approved (or that were
auto-approved by the platform's read-only policy). Output is capped at
``output_byte_limit`` (the agent sets 4 MiB) with head-truncation to keep the
retained tail, mirroring the protocol's truncation contract.

Each terminal runs in its own process group (``start_new_session=True``);
kill/release terminate the whole group so pipelines and background children
of an approved Bash command cannot outlive the terminal. Group identity is
re-validated right before the signal: the pid is compared against the
process's own pgid, so a recycled or already-reaped pid can never aim the
group signal at an unrelated process (AGENTS.md killpg discipline).

Lifecycle: terminals live in a per-handle registry on the session loop's
event loop; ``release``/``kill`` reap the process, and ``AcpTerminalStore.
close_all`` (called from the handle teardown path) kills anything the agent
forgot to release.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal as signal_module
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from acp.schema import (
    CreateTerminalResponse,
    KillTerminalResponse,
    ReleaseTerminalResponse,
    TerminalExitStatus,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
)

if TYPE_CHECKING:
    from server.app.studio_chat.acp_session import AcpSessionHandle

logger = logging.getLogger(__name__)

# Cap for agents that omit outputByteLimit (kimi always sends 4 MiB).
DEFAULT_OUTPUT_BYTE_LIMIT = 4 * 1024 * 1024
# 256 KiB floor: even a pathological 0-limit request keeps a usable tail.
MIN_OUTPUT_BYTE_LIMIT = 256 * 1024
# Reap guard so a wedged child cannot hold the wait forever.
WAIT_TIMEOUT_SECONDS = 3600


@dataclass
class _Terminal:
    process: asyncio.subprocess.Process
    output: bytearray = field(default_factory=bytearray)
    byte_limit: int = DEFAULT_OUTPUT_BYTE_LIMIT
    truncated: bool = False
    released: bool = False


class AcpTerminalStore:
    """Live terminals for one ACP session handle; owned by the session loop."""

    def __init__(self) -> None:
        self._terminals: dict[str, _Terminal] = {}

    async def create(
        self,
        *,
        command: str,
        args: list[str] | None,
        env: list[Any] | None,
        cwd: str | None,
        output_byte_limit: int | None,
        default_cwd: str,
    ) -> CreateTerminalResponse:
        terminal_id = uuid4().hex
        limit = output_byte_limit or DEFAULT_OUTPUT_BYTE_LIMIT
        limit = max(limit, MIN_OUTPUT_BYTE_LIMIT)
        # env arrives as EnvVariable models (name/value); None means inherit.
        process_env: dict[str, str] | None = None
        if env:
            process_env = {str(item.name): str(item.value) for item in env}
        process = await asyncio.create_subprocess_exec(
            command,
            *(args or []),
            cwd=cwd or default_cwd,
            env=process_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Own process group: kill/release must take down the whole tree
            # (pipelines, `&` background children), not just the direct child.
            start_new_session=True,
        )
        terminal = _Terminal(process=process, byte_limit=limit)
        self._terminals[terminal_id] = terminal
        asyncio.get_running_loop().create_task(self._drain(terminal_id, terminal))
        return CreateTerminalResponse(terminal_id=terminal_id)

    async def output(self, terminal_id: str) -> TerminalOutputResponse:
        terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise KeyError(f"unknown terminal: {terminal_id}")
        exit_status: TerminalExitStatus | None = None
        if terminal.process.returncode is not None:
            exit_status = TerminalExitStatus(**_exit_payload(terminal.process))
        return TerminalOutputResponse(
            output=bytes(terminal.output).decode("utf-8", errors="replace"),
            truncated=terminal.truncated,
            exit_status=exit_status,
        )

    async def wait_for_exit(self, terminal_id: str) -> WaitForTerminalExitResponse:
        terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise KeyError(f"unknown terminal: {terminal_id}")
        try:
            await asyncio.wait_for(terminal.process.wait(), timeout=WAIT_TIMEOUT_SECONDS)
        except TimeoutError:
            await self._kill(terminal)
        return WaitForTerminalExitResponse(**_exit_payload(terminal.process))

    async def release(self, terminal_id: str) -> ReleaseTerminalResponse:
        terminal = self._terminals.pop(terminal_id, None)
        if terminal is None:
            # Idempotent per protocol: releasing twice is not an error.
            return ReleaseTerminalResponse()
        terminal.released = True
        if terminal.process.returncode is None:
            await self._kill(terminal)
        return ReleaseTerminalResponse()

    async def kill(self, terminal_id: str) -> None:
        terminal = self._terminals.get(terminal_id)
        if terminal is None:
            return
        await self._kill(terminal)

    async def close_all(self) -> None:
        """Handle teardown: kill every terminal the agent left behind."""
        for terminal_id in list(self._terminals):
            with _swallow("terminal close_all"):
                await self.release(terminal_id)

    async def _drain(self, terminal_id: str, terminal: _Terminal) -> None:
        """Fold stdout(+stderr) into the capped output buffer; head-truncate."""
        del terminal_id
        stream = terminal.process.stdout
        if stream is None:
            return
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return
            terminal.output.extend(chunk)
            if len(terminal.output) > terminal.byte_limit:
                # Keep the tail: the interesting part of a long command run
                # is usually how it ended.
                del terminal.output[: len(terminal.output) - terminal.byte_limit]
                terminal.truncated = True

    async def _kill(self, terminal: _Terminal) -> None:
        if terminal.process.returncode is not None:
            return
        # Group-wide signal, identity-checked: start_new_session made the child
        # its own group leader (pid == pgid), so this only matches while the
        # child is still that leader. A recycled pid in another group fails the
        # pgid comparison and the signal is skipped instead of hitting an
        # unrelated process (AGENTS.md killpg re-validation discipline).
        with _swallow("terminal killpg"):
            pid = terminal.process.pid
            if pid is not None and os.getpgid(pid) == pid:
                os.killpg(pid, signal_module.SIGKILL)
        with _swallow("terminal kill wait"):
            await terminal.process.wait()


class _swallow:
    """Log-and-continue context manager for teardown-path failures."""

    def __init__(self, what: str) -> None:
        self._what = what

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc is not None:
            logger.warning("studio chat %s failed: %s", self._what, exc)
        return True


def _exit_payload(process: asyncio.subprocess.Process) -> dict[str, Any]:
    """Map a POSIX returncode onto the protocol's exitCode/signal pair.

    asyncio reports signal deaths as negative returncodes (-9 = SIGKILL), but
    the schema validates exitCode >= 0 (and its salvage validator silently
    turns invalid values into None). A signal kill therefore maps to
    exitCode=None + the signal name, which is exactly what the protocol docs
    prescribe for "terminated by signal".
    """
    code = process.returncode
    if code is None or code >= 0:
        return {"exit_code": code}
    try:
        name = signal_module.Signals(-code).name
    except ValueError:
        name = str(-code)
    return {"exit_code": None, "signal": name}


class TerminalClientMixin:
    """``terminal/*`` request handlers for the ACP client object.

    The ACP SDK router resolves ``create_terminal`` / ``terminal_output`` /
    ``wait_for_terminal_exit`` / ``release_terminal`` / ``kill_terminal`` as
    attributes on the client object (duck-typed protocol) and calls them with
    the request models' field names. The host class provides ``terminals``
    (an AcpTerminalStore) and ``_handle`` (for the default cwd); this mixin
    lives in terminals.py so acp_session.py stays within its size budget.
    """

    terminals: AcpTerminalStore
    _handle: AcpSessionHandle

    async def create_terminal(  # type: ignore[no-untyped-def]
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[Any] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        del session_id  # one store per handle; the id adds nothing here
        return await self.terminals.create(
            command=command,
            args=args,
            env=env,
            cwd=cwd,
            output_byte_limit=output_byte_limit,
            default_cwd=self._handle.cwd,
        )

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> TerminalOutputResponse:
        del session_id
        return await self.terminals.output(terminal_id)

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        del session_id
        return await self.terminals.wait_for_exit(terminal_id)

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse:
        del session_id
        return await self.terminals.release(terminal_id)

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> KillTerminalResponse:
        del session_id
        await self.terminals.kill(terminal_id)
        return KillTerminalResponse()
