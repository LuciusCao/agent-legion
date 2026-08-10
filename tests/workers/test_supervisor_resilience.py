"""Supervisor stop/start resilience tests (worker/supervisor.py).

Covers: stop tolerating a corrupt config file, the claim_enabled reset log
line (deliberate design, now visible), and orphan agent process-group
reaping after the executor is killed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import wait_for_predicate
from worker.process_lifecycle import AGENT_PGID_FILENAME
from worker.supervisor import WorkerConfigStore, WorkerSupervisor, validate_config

pytestmark = pytest.mark.no_db

_FAKE_WORKER = 'import time\nprint("fake worker ready", flush=True)\ntime.sleep(30)\n'


def _config(token_file: Path, work_root: Path) -> dict[str, Any]:
    return {
        "host_url": "http://host.test:8000/",
        "worker_id": "worker-1",
        "runtimes": ["pi"],
        "max_concurrency": 1,
        "register_token_file": str(token_file),
        "work_root": str(work_root),
        "shutdown_grace_seconds": 25,
    }


def _make_supervisor(tmp_path: Path) -> tuple[WorkerSupervisor, Path]:
    script = tmp_path / "fake_worker.py"
    script.write_text(_FAKE_WORKER, encoding="utf-8")
    token_file = tmp_path / "register-token"
    token_file.write_text("secret", encoding="utf-8")
    work_root = tmp_path / "work"
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config(token_file, work_root)))
    return WorkerSupervisor(store, script), work_root


def test_start_logs_claim_disabled_reset(tmp_path: Path) -> None:
    supervisor, _ = _make_supervisor(tmp_path)
    supervisor.start()
    try:
        wait_for_predicate(lambda: supervisor.running())
        assert any("claim_enabled" in line for line in supervisor.logs())
    finally:
        supervisor.stop()


def test_stop_tolerates_corrupt_config(tmp_path: Path) -> None:
    supervisor, _ = _make_supervisor(tmp_path)
    supervisor.start()
    wait_for_predicate(lambda: supervisor.running())

    supervisor.store.path.write_text("claim_enabled: [unclosed", encoding="utf-8")
    supervisor.stop()  # 配置损坏不得让 stop 500：进程能停即可

    wait_for_predicate(lambda: not supervisor.running())
    assert supervisor.running() is False


def test_stop_reaps_orphaned_agent_process_groups(tmp_path: Path) -> None:
    supervisor, work_root = _make_supervisor(tmp_path)
    orphan = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    record_dir = work_root / "exec-orphan"
    record_dir.mkdir(parents=True)
    (record_dir / AGENT_PGID_FILENAME).write_text(str(orphan.pid), encoding="utf-8")
    try:
        supervisor.start()
        wait_for_predicate(lambda: supervisor.running())
        supervisor.stop()
        orphan.wait(timeout=15)
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait(timeout=5)
        supervisor.stop()

    assert orphan.poll() is not None
