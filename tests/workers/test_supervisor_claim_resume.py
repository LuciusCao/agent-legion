"""#681: a crash auto-restart keeps the operator's claim switch (bounded).

Root cause of the incident's hours-long idle: the executor was SIGKILLed
(VM OOM), the supervisor auto-restarted it, and ``_start`` reset
``claim_enabled`` to false exactly like a cold start — the Host requeued the
lost leases and the only online Worker never claimed again. Cold start and
manual restart keep the deliberate pause; crash loops and the rolling resume
cap fall back to it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import worker.supervisor as supervisor_module
from tests.helpers import wait_for_predicate
from worker.restart_policy import (
    CLAIM_RESUME_LIMIT,
    CLAIM_RESUME_WINDOW_SECONDS,
    claim_resume_verdict,
)
from worker.supervisor import WorkerConfigStore, WorkerSupervisor, validate_config

pytestmark = pytest.mark.no_db

# The first run SIGKILLs itself once the test drops ``crash-now`` (the
# OOM-killer exit -9 of the incident); every later run stays up.
_FAKE_WORKER = """
import os, signal, sys, time
from pathlib import Path
here = Path(sys.argv[0]).parent
print("fake worker ready", flush=True)
if not (here / "crashed-once").exists():
    while not (here / "crash-now").exists():
        time.sleep(0.02)
    (here / "crashed-once").write_text("x")
    os.kill(os.getpid(), signal.SIGKILL)
time.sleep(30)
"""


def _supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkerSupervisor:
    script = tmp_path / "fake_worker.py"
    script.write_text(_FAKE_WORKER, encoding="utf-8")
    token_file = tmp_path / "register-token"
    token_file.write_text("secret", encoding="utf-8")
    config: dict[str, Any] = {
        "host_url": "http://host.test:8000/",
        "worker_id": "worker-1",
        "runtimes": ["pi"],
        "max_concurrency": 1,
        "register_token_file": str(token_file),
        "work_root": str(tmp_path / "work"),
        "shutdown_grace_seconds": 25,
    }
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(config))
    monkeypatch.setattr(supervisor_module, "_RESTART_BACKOFF_INITIAL", 0.05)
    return WorkerSupervisor(store, script)


def _claim_enabled(supervisor: WorkerSupervisor) -> bool:
    return bool(supervisor.store.read(require_identity=False)["claim_enabled"])


def test_crash_restart_after_stable_run_keeps_claims_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复现 #681 空转根因：操作员开启认领后 executor 被 SIGKILL，自动重启
    后认领必须仍开启（base 上被重置为 false → 唯一 worker 永不再 claim）。"""
    # Every run counts as stable: the crash is the first after a stable run.
    monkeypatch.setattr(supervisor_module, "_STABLE_AFTER", 0.0)
    supervisor = _supervisor(tmp_path, monkeypatch)
    supervisor.start()
    try:
        assert _claim_enabled(supervisor) is False  # cold start still pauses
        supervisor.store.update_public({"claim_enabled": True})  # operator
        (tmp_path / "crash-now").write_text("x")
        wait_for_predicate(lambda: supervisor.status()["restart_count"] >= 1)
        wait_for_predicate(lambda: supervisor.running())
        wait_for_predicate(lambda: any("保留已开启的认领" in line for line in supervisor.logs()))
        assert _claim_enabled(supervisor) is True
        assert any("退出码 -9" in line for line in supervisor.logs())
    finally:
        supervisor.stop()


def test_manual_restart_still_resets_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手动 restart（操作员动作）保持既有刻意设计：重置为 false。"""
    (tmp_path / "crashed-once").write_text("x")  # no crash in this test
    supervisor = _supervisor(tmp_path, monkeypatch)
    supervisor.start()
    try:
        wait_for_predicate(lambda: supervisor.running())
        supervisor.store.update_public({"claim_enabled": True})
        supervisor.restart()
        wait_for_predicate(lambda: supervisor.running())
        assert _claim_enabled(supervisor) is False
    finally:
        supervisor.stop()


def test_verdict_crash_loop_pauses_claims() -> None:
    """崩溃循环（上一个 executor 未稳定运行，restart_count>1）回落暂停。"""
    keep, message = claim_resume_verdict(True, 2, [], 100.0)
    assert keep is False
    assert "反复崩溃" in message


def test_verdict_rolling_cap_pauses_claims() -> None:
    """滚动窗口内保留次数达到上限即回落暂停；窗口外的旧记录不计数。"""
    now = 10_000.0
    recent = [now - 10.0 * index for index in range(CLAIM_RESUME_LIMIT)]
    keep, message = claim_resume_verdict(True, 1, recent, now)
    assert keep is False
    assert "重置为 false" in message
    expired = [now - CLAIM_RESUME_WINDOW_SECONDS - 1.0] * CLAIM_RESUME_LIMIT
    assert claim_resume_verdict(True, 1, expired, now)[0] is True


def test_verdict_disabled_switch_stays_disabled() -> None:
    """操作员本就关着认领：崩溃重启不会替他打开。"""
    keep, _ = claim_resume_verdict(False, 1, [], 0.0)
    assert keep is False
