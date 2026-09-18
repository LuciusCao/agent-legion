"""Unit tests for the ACP terminal store (studio chat Bash/Grep backing)."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from server.app.studio_chat.terminal_guard import TerminalCommandBlockedError
from server.app.studio_chat.terminals import AcpTerminalStore

# Pure subprocess unit tests: no database access, skip TRUNCATE isolation.
pytestmark = pytest.mark.no_db


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
            args=[
                "-c",
                "import os; print(os.environ.get('STUDIO_TEST_MARKER', 'missing')); "
                "print('PATH' in os.environ and 'path-kept' or 'path-lost')",
            ],
            env=[_Env("STUDIO_TEST_MARKER", "present")],
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        await store.wait_for_exit(created.terminalId)
        state = await store.output(created.terminalId)
        assert "present" in state.output
        # env 是合并而非替换：agent 只传覆盖项时继承环境（PATH 等）不丢。
        assert "path-kept" in state.output

    asyncio.run(_run())


def test_kill_takes_down_the_whole_process_group() -> None:
    """`sh -c 'sleep 60 & sleep 60'` 之类命令的后台子进程必须随 terminal
    一起终止——只杀直接子进程会让后代进程泄漏成孤儿。"""
    import shutil

    async def _run() -> None:
        if shutil.which("sh") is None:
            return
        store = AcpTerminalStore()
        created = await store.create(
            command="sh",
            args=["-c", "sleep 987 & sleep 987 & wait"],
            env=None,
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        await asyncio.sleep(0.5)
        terminal = store._terminals[created.terminalId]
        pgid = os.getpgid(terminal.process.pid)
        # The group already has the direct child plus two sleeps.
        # (macOS/Linux `ps` per-group listing; count what our group holds.)
        await store.kill(created.terminalId)
        awaited = await store.wait_for_exit(created.terminalId)
        assert awaited.exit_code is None  # group SIGKILL reads as signal death
        await asyncio.sleep(0.3)
        # After the group kill no member of the old pgid may survive.
        import subprocess

        listing = subprocess.run(
            ["ps", "-o", "pgid=", "-eo", "pgid,pid"], capture_output=True, text=True
        )
        surviving = [
            line.split()
            for line in listing.stdout.splitlines()
            if line.split() and line.split()[0] == str(pgid)
        ]
        assert surviving == []
        await store.release(created.terminalId)

    asyncio.run(_run())


def test_create_refuses_service_lifecycle_shell_commands() -> None:
    """#629：terminal/create 对服务生命周期命令在 spawn 前拒绝——审批链
    （人类已批准的 Bash 工具调用）之上叠平台级硬防线。拒绝以异常抛出，
    经 acp SDK 变成 terminal/create 的 JSON-RPC error 让 agent 的 Bash
    工具收到失败结果；错误信息指路人工入口 ./scripts/prod-restart.sh。"""

    async def _run() -> None:
        store = AcpTerminalStore()
        # 事故原始形态：shell -c 的完整命令串（kimi Bash 工具的实际路径）。
        with pytest.raises(TerminalCommandBlockedError) as exc_info:
            await store.create(
                command="sh",
                args=["-c", "make prod-down && make prod-up"],
                env=None,
                cwd=None,
                output_byte_limit=None,
                default_cwd=".",
            )
        assert "prod-restart.sh" in str(exc_info.value)
        # 链中位置无关：prod-down 在链尾同样拒绝。
        with pytest.raises(TerminalCommandBlockedError):
            await store.create(
                command="sh",
                args=["-c", "echo start; make prod-down"],
                env=None,
                cwd=None,
                output_byte_limit=None,
                default_cwd=".",
            )
        # 直接 exec 形态：command 本身是脚本。
        with pytest.raises(TerminalCommandBlockedError):
            await store.create(
                command="./scripts/native-prod-down.sh",
                args=None,
                env=None,
                cwd=None,
                output_byte_limit=None,
                default_cwd=".",
            )
        # 拒绝发生在 spawn 前：注册表里没有任何 terminal 留下。
        assert not store._terminals

    asyncio.run(_run())


def test_create_refuses_env_injection_despite_clean_argv() -> None:
    """#707 HIGH-4：env 覆盖是 argv 之外的第二命令通道——BASH_ENV 让非交互
    bash 在被检查的命令之前 source 攻击脚本（实测：BASH_ENV=x.sh bash -c 先
    输出脚本内容再跑命令）。guard 拒绝发生在 spawn 前，且注册表无残留。"""

    async def _run() -> None:
        store = AcpTerminalStore()

        class _Env:
            def __init__(self, name: str, value: str) -> None:
                self.name = name
                self.value = value

        # argv 完全干净，注入藏在 env。
        with pytest.raises(TerminalCommandBlockedError) as exc_info:
            await store.create(
                command="bash",
                args=["-c", "echo hi"],
                env=[_Env("BASH_ENV", "/tmp/evil.sh")],
                cwd=None,
                output_byte_limit=None,
                default_cwd=".",
            )
        assert "BASH_ENV" in str(exc_info.value)
        # PATH 含相对段（. 或空段）同样拒绝：CWD 内的假 make 影子解析。
        with pytest.raises(TerminalCommandBlockedError):
            await store.create(
                command="make",
                args=["check"],
                env=[_Env("PATH", "/tmp/evil:.")],
                cwd=None,
                output_byte_limit=None,
                default_cwd=".",
            )
        assert not store._terminals

    asyncio.run(_run())


def test_create_allows_string_mentions_of_blocked_names() -> None:
    """拒绝是命令级匹配而不是子串匹配：字符串里提到 prod-down 的普通
    命令照常执行（误伤面反例）。"""

    async def _run() -> None:
        store = AcpTerminalStore()
        created = await store.create(
            command=sys.executable,
            args=["-c", "print('run make prod-down manually')"],
            env=None,
            cwd=None,
            output_byte_limit=None,
            default_cwd=".",
        )
        awaited = await store.wait_for_exit(created.terminalId)
        assert awaited.exit_code == 0
        state = await store.output(created.terminalId)
        assert "prod-down" in state.output

    asyncio.run(_run())
