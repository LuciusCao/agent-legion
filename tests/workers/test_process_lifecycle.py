"""worker/process_lifecycle.terminate 的 killpg 边缘异常单测（issue #640）。

killpg 对已退出/换组的进程组会抛 EPERM（PermissionError）或 ESRCH
（ProcessLookupError）。terminate 是所有执行收尾路径（run_execution 的
finally / shutdown / cancel / 超时）的公共出口，两种失败都必须按
「进程已不可达」处理：不抛异常、照常继续 wait 确认/返回——修复前
EPERM 未被兜住，会沿收尾调用链炸穿执行线程并拖垮整个 executor。
"""

from __future__ import annotations

import os
import signal
import subprocess

import pytest

from worker.process_lifecycle import terminate


class _FakeProc:
    """wait 行为可编程的假子进程：钉住 killpg 失败后 terminate 仍走 wait。"""

    def __init__(self, wait_error: Exception | None = None) -> None:
        self.pid = 999_999_999
        self.wait_error = wait_error
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.wait_error is not None:
            raise self.wait_error
        return 0


def _killpg_always_raising(monkeypatch: pytest.MonkeyPatch, exc: OSError) -> list[tuple[int, int]]:
    """把 os.killpg 换成记录调用后固定抛 exc 的替身，返回调用记录。"""
    calls: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        raise exc

    monkeypatch.setattr(os, "killpg", fake_killpg)
    return calls


def test_terminate_treats_killpg_eperm_as_gone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """EPERM（#640 主场景）：不抛、两档信号照送、每档失败后仍走 wait。"""
    killpg_calls = _killpg_always_raising(
        monkeypatch, PermissionError(1, "Operation not permitted")
    )
    proc = _FakeProc(wait_error=subprocess.TimeoutExpired("cmd", 0.01))

    terminate(proc, 0.01)  # 修复前 PermissionError 在此处上抛

    assert [sig for _, sig in killpg_calls] == [signal.SIGTERM, signal.SIGKILL]
    assert proc.wait_calls == [0.01, 0.01]
    out = capsys.readouterr().out
    assert "killpg" in out, "进程组不可达必须留一行日志，不能静默"


def test_terminate_treats_killpg_esrch_as_gone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ESRCH（进程组已消失）：原 suppress 语义保留——不抛、继续 wait。"""
    killpg_calls = _killpg_always_raising(monkeypatch, ProcessLookupError(3, "No such process"))
    proc = _FakeProc(wait_error=subprocess.TimeoutExpired("cmd", 0.01))

    terminate(proc, 0.01)

    assert [sig for _, sig in killpg_calls] == [signal.SIGTERM, signal.SIGKILL]
    assert proc.wait_calls == [0.01, 0.01]
    assert "killpg" in capsys.readouterr().out


def test_terminate_returns_once_wait_confirms_exit_after_eperm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """killpg EPERM 但子进程确已退出：首个 wait 即确认返回，不发第二档信号。"""
    killpg_calls = _killpg_always_raising(
        monkeypatch, PermissionError(1, "Operation not permitted")
    )
    proc = _FakeProc()  # wait 直接返回 0（进程早已死透）

    terminate(proc, 5)

    assert [sig for _, sig in killpg_calls] == [signal.SIGTERM]
    assert proc.wait_calls == [5]
    assert "did not exit" not in capsys.readouterr().out


def test_terminate_treats_wait_oserror_as_gone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """wait 自身的非超时 OSError（防御性兜底）：按已消失返回，不上抛。"""
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: None)
    proc = _FakeProc(wait_error=OSError(10, "No child processes"))

    terminate(proc, 0.01)

    assert proc.wait_calls == [0.01]
    assert "treating it as gone" in capsys.readouterr().out
