"""Unit tests for the ACP terminal store (studio chat Bash/Grep backing)."""

from __future__ import annotations

import asyncio
import sys

import pytest

from server.app.studio_chat.terminals import AcpTerminalStore


def test_create_output_wait_release_roundtrip() -> None:
    async def _run() -> None:
        store = AcpTerminalStore()
        created = await store.create(
            command=sys.executable,
            args=["-c", "print('hello terminal')"],
            env=None,
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        terminal_id = created.terminalId

        awaited = await store.wait_for_exit(terminal_id)
        assert awaited.exit_code == 0

        state = await store.output(terminal_id)
        assert "hello terminal" in state.output
        assert state.truncated is False
        assert state.exitStatus is not None
        assert state.exitStatus.exit_code == 0

        # Release is idempotent and clears the registry.
        await store.release(terminal_id)
        await store.release(terminal_id)
        with pytest.raises(KeyError):
            await store.output(terminal_id)

    asyncio.run(_run())


def test_output_reports_nonzero_exit_and_merged_stderr() -> None:
    async def _run() -> None:
        store = AcpTerminalStore()
        created = await store.create(
            command=sys.executable,
            args=["-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"],
            env=None,
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        awaited = await store.wait_for_exit(created.terminalId)
        assert awaited.exit_code == 3
        state = await store.output(created.terminalId)
        # stderr folds into the same output stream (stdio=STDOUT).
        assert "boom" in state.output
        assert state.exitStatus is not None
        assert state.exitStatus.exit_code == 3

    asyncio.run(_run())


def test_output_byte_limit_keeps_tail_and_marks_truncated() -> None:
    async def _run() -> None:
        store = AcpTerminalStore()
        # 6000 lines x ~100 bytes = ~600 KB, capped to the 256 KiB floor.
        payload = "for i in range(6000): print(f'{i:096d}')"
        created = await store.create(
            command=sys.executable,
            args=["-c", payload],
            env=None,
            cwd=None,
            output_byte_limit=1,  # below the floor: clamps to 256 KiB
            default_cwd=".",
        )
        await store.wait_for_exit(created.terminalId)
        state = await store.output(created.terminalId)
        assert state.truncated is True
        # The retained output is the TAIL: the last line is present, the first is gone.
        assert "000005999" in state.output
        assert f"{0:096d}\n" not in state.output
        assert len(state.output.encode()) <= 256 * 1024 + 200  # one chunk of slack

    asyncio.run(_run())


def test_kill_stops_a_long_running_terminal() -> None:
    async def _run() -> None:
        store = AcpTerminalStore()
        created = await store.create(
            command=sys.executable,
            args=["-c", "import time; time.sleep(60)"],
            env=None,
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        await asyncio.sleep(0.3)
        await store.kill(created.terminalId)
        awaited = await store.wait_for_exit(created.terminalId)
        # Signal death maps to exitCode=None + signal name (schema rejects negatives).
        assert awaited.exit_code is None
        assert awaited.signal is not None
        state = await store.output(created.terminalId)
        assert state.exitStatus is not None
        assert state.exitStatus.exit_code is None
        await store.release(created.terminalId)

    asyncio.run(_run())


def test_release_kills_unfinished_process_and_close_all_reaps_rest() -> None:
    async def _run() -> None:
        store = AcpTerminalStore()
        first = await store.create(
            command=sys.executable,
            args=["-c", "import time; time.sleep(60)"],
            env=None,
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        second = await store.create(
            command=sys.executable,
            args=["-c", "import time; time.sleep(60)"],
            env=None,
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        # release kills the first; close_all reaps the second.
        await store.release(first.terminalId)
        await store.close_all()
        with pytest.raises(KeyError):
            await store.output(second.terminalId)

    asyncio.run(_run())


def test_env_vars_are_passed_to_the_process() -> None:
    async def _run() -> None:
        store = AcpTerminalStore()

        class _Env:
            def __init__(self, name: str, value: str) -> None:
                self.name = name
                self.value = value

        created = await store.create(
            command=sys.executable,
            args=["-c", "import os; print(os.environ.get('STUDIO_TEST_MARKER', 'missing'))"],
            env=[_Env("STUDIO_TEST_MARKER", "present")],
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        await store.wait_for_exit(created.terminalId)
        state = await store.output(created.terminalId)
        assert "present" in state.output

    asyncio.run(_run())
