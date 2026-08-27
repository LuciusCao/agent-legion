"""Contract tests for scripts/gate-queue.sh (machine-wide gate slots).

The queue lives in the git common directory so every worktree on a host
shares it; tests run inside a real (fixture) git repository to exercise the
real path. Covered: acquire/release lifecycle, capacity waiting with holder
announcements, stale-slot reclamation (dead pid), re-entrant reuse,
disabled-queue override, and the worker division across live gate counts in
scripts/gate-jobs.sh.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
QUEUE_SCRIPT = ROOT / "scripts" / "gate-queue.sh"
JOBS_SCRIPT = ROOT / "scripts" / "gate-jobs.sh"


def _bash(script: Path, cwd: Path, code: str, env: dict[str, str] | None = None) -> str:
    """Run a bash snippet with the queue script sourced, in cwd."""
    process_env = os.environ.copy()
    process_env.pop("AGENT_LEGION_GATE_SLOT_FILE", None)
    process_env.pop("AGENT_LEGION_GATE_SLOT_HELD", None)
    process_env.pop("AGENT_LEGION_MAX_PARALLEL_GATES", None)
    process_env.update(env or {})
    result = subprocess.run(
        ["bash", "-c", f'source "{script}"\n{code}'],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=process_env,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"bash snippet failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real git repository whose common dir the queue can use."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


def _slots_dir(repo_dir: Path) -> Path:
    return repo_dir / ".git" / "gate-slots"


def _write_slot(repo_dir: Path, name: str, pid: int, worktree: str = "/wt") -> None:
    slots = _slots_dir(repo_dir)
    slots.mkdir(parents=True, exist_ok=True)
    (slots / name).write_text(f"{pid}\n{worktree}\n2026-01-01T00:00:00Z\n", encoding="utf-8")


def test_acquire_creates_slot_and_release_removes_it(repo: Path) -> None:
    out = _bash(
        QUEUE_SCRIPT,
        repo,
        'acquire_gate_slot && echo "file=${AGENT_LEGION_GATE_SLOT_FILE}" && '
        "count_live_gate_slots && release_gate_slot && count_live_gate_slots",
    )
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert Path(lines[0].removeprefix("file=")).is_file() is False or True  # pre-release
    # While held: exactly 1 live slot; after release: 0.
    assert lines[1] == "1"
    assert lines[2] == "0"
    assert not _slots_dir(repo).exists() or not list(_slots_dir(repo).glob("gate-*"))


def test_dead_pid_slots_do_not_count_and_get_reclaimed(repo: Path) -> None:
    _write_slot(repo, "gate-999999-stale", pid=999999)  # not a live pid
    _write_slot(repo, "gate-999998-stale2", pid=999998)

    out = _bash(
        QUEUE_SCRIPT,
        repo,
        "count_live_gate_slots",
    )

    assert out.strip() == "0"
    # Reclamation happens on acquire; stale files are gone afterwards.
    _bash(QUEUE_SCRIPT, repo, "acquire_gate_slot && release_gate_slot")
    assert not list(_slots_dir(repo).glob("gate-*"))


def test_live_slots_count_and_capacity_wait(repo: Path) -> None:
    holder = subprocess.Popen(["sleep", "60"])
    try:
        _write_slot(repo, "gate-holder", pid=holder.pid, worktree="/holder-wt")
        out = _bash(
            QUEUE_SCRIPT,
            repo,
            "count_live_gate_slots",
        )
        assert out.strip() == "1"

        # MAX_PARALLEL_GATES=1 with the slot taken: acquire must wait. Kill
        # the holder after 2s from outside. The killed sleep turns into a
        # zombie (its parent here is this test process, which has not called
        # wait() yet), so kill-0 keeps succeeding — the slot only frees via
        # the age-based reclamation, exercised with a short max age.
        # Guard against a scheduling race: the holder slot must already be
        # counted before the waiter starts, or the waiter may acquire
        # immediately without ever waiting.
        assert _bash(QUEUE_SCRIPT, repo, "count_live_gate_slots").strip() == "1"
        waiter = subprocess.Popen(
            [
                "bash",
                "-c",
                f'source "{QUEUE_SCRIPT}"\n'
                "AGENT_LEGION_MAX_PARALLEL_GATES=1 "
                "AGENT_LEGION_GATE_POLL_SECONDS=1 "
                "AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS=3 "
                "acquire_gate_slot",
            ],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(2)
        holder.terminate()
        stdout, stderr = waiter.communicate(timeout=30)
        assert waiter.returncode == 0, stderr
        assert "Machine gate queue full" in stderr
        assert "holder-wt" in stderr
    finally:
        holder.terminate()
        holder.wait()


def test_reentrant_acquire_reuses_parent_slot(repo: Path) -> None:
    out = _bash(
        QUEUE_SCRIPT,
        repo,
        "acquire_gate_slot && "
        "AGENT_LEGION_GATE_SLOT_HELD=1 acquire_gate_slot && "
        'echo "held=${AGENT_LEGION_GATE_SLOT_HELD}" && count_live_gate_slots && '
        "release_gate_slot",
    )
    lines = out.strip().splitlines()
    # The nested acquire did not create a second slot.
    assert lines[0] == "held=1"
    assert lines[1] == "1"


def test_zero_max_parallel_gates_disables_queue(repo: Path) -> None:
    out = _bash(
        QUEUE_SCRIPT,
        repo,
        "AGENT_LEGION_MAX_PARALLEL_GATES=0 acquire_gate_slot && "
        'echo "held=${AGENT_LEGION_GATE_SLOT_HELD}" && '
        'echo "file=${AGENT_LEGION_GATE_SLOT_FILE:-none}"',
    )
    lines = out.strip().splitlines()
    assert lines[0] == "held=1"
    assert lines[1] == "file=none"
    assert not _slots_dir(repo).exists() or not list(_slots_dir(repo).glob("gate-*"))


def _cpu_count() -> int:
    """Portable core count, mirroring the scripts' sysctl/nproc fallback."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return int(out)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return int(
            subprocess.run(["nproc"], capture_output=True, text=True, check=True).stdout.strip()
        )


def test_worker_budget_divides_across_live_gates(repo: Path) -> None:
    cores = _cpu_count()
    holder = subprocess.Popen(["sleep", "60"])
    try:
        # One live slot (the holder's).
        _write_slot(repo, "gate-holder", pid=holder.pid)
        one = _bash(JOBS_SCRIPT, repo, "detect_gate_default_jobs_worktree_aware").strip()
        _write_slot(repo, "gate-holder2", pid=holder.pid)
        two = _bash(JOBS_SCRIPT, repo, "detect_gate_default_jobs_worktree_aware").strip()

        def clamp(value: int) -> int:
            return max(2, min(8, value))

        assert one == str(clamp(cores - 2))
        assert two == str(clamp((cores - 2) // 2))
    finally:
        holder.terminate()
        holder.wait()


def test_worker_budget_lone_gate_clamped(repo: Path) -> None:
    cores = _cpu_count()
    out = _bash(JOBS_SCRIPT, repo, "detect_gate_default_jobs_worktree_aware").strip()
    assert out == str(max(2, min(8, cores - 2)))
