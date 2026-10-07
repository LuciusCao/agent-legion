"""dev_stack.sh stop_port waits for the process to exit, not just the port (#760).

uvicorn closes its listener first and only then drains SSE connections and
the lifespan; a stop that returned as soon as the port went quiet let the
next dev-up overlap the still-exiting backend. Behavioural stub test: the
function is extracted from the script, ``lsof`` / ``ps`` are PATH stubs that
point at a throwaway target process (ppid stubbed to 1 so nothing else is
signalled).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
DEV_STACK = ROOT / "scripts" / "dev_stack.sh"

# Exits ``delay`` seconds after SIGTERM (negative: ignores SIGTERM).
_TARGET = """
import signal, sys, time
delay = float(sys.argv[1])
def on_term(*_):
    if delay < 0:
        return
    time.sleep(delay)
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
print("ready", flush=True)
while True:
    time.sleep(0.1)
"""


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _start_target(delay: float) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        [sys.executable, "-c", _TARGET, str(delay)], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None and proc.stdout.readline().strip() == "ready"
    # Reap on exit so kill -0 does not see a zombie.
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def _stop_port(tmp_path: Path, pid: int, grace: int) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub(bin_dir, "lsof", f"echo {pid}")
    _stub(bin_dir, "ps", "echo 1")
    script = (
        f"LOG_DIR={tmp_path}; "
        f"eval \"$(sed -n '/^stop_port() {{/,/^}}/p' '{DEV_STACK}')\"; "
        f"stop_port 59999 后端 {grace}"
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30
    )


def test_stop_port_waits_for_the_process_to_exit(tmp_path) -> None:
    proc = _start_target(delay=2.0)
    started = time.monotonic()
    result = _stop_port(tmp_path, proc.pid, grace=10)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert "已停止" in result.stdout
    # The listener stub never "closes"; returning at all means it watched
    # the pid, and only after the post-TERM drain finished.
    assert elapsed >= 1.5
    proc.wait(timeout=5)


def test_stop_port_times_out_with_a_warning(tmp_path) -> None:
    proc = _start_target(delay=-1)
    try:
        result = _stop_port(tmp_path, proc.pid, grace=1)
        assert result.returncode == 1
        assert "未退出" in result.stderr
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)
