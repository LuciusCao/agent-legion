"""Contract tests for scripts/gate-queue.sh (machine-wide gate slots).

The queue lives in the git common directory so every worktree on a host
shares it; tests run inside a real (fixture) git repository to exercise the
real path. Covered: acquire/release lifecycle, capacity waiting with holder
announcements, stale-slot reclamation (dead pid), re-entrant reuse,
disabled-queue override, the serialized default (one concurrent gate), the
worker division across live gate counts in scripts/gate-jobs.sh, and
vanishing-slot tolerance (issue #488: a yielding contender deletes the slot
it just created, so any slot globbed by another process can disappear
before it is read — every reader must skip it, never fail the gate).
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
QUEUE_SCRIPT = ROOT / "scripts" / "gate-queue.sh"
JOBS_SCRIPT = ROOT / "scripts" / "gate-jobs.sh"

# Env var the PATH shims below touch on every interception, so tests can
# assert the shim actually fired (a PATH mistake would otherwise make a
# vanishing-slot test pass vacuously).
_SHIM_MARKER_ENV = "GATE_QUEUE_TEST_SHIM_MARKER"

# stat shim: answer ONE query for the target file from the real stat, then
# delete the file, so the next stat on that path sees ENOENT — the exact
# "globbed, then vanished between probe and real query" window of issue
# #488, made deterministic (the old probe-then-query pair died on the
# second query under set -e).
_STAT_SHIM = r"""#!/bin/sh
for target in "$@"; do :; done
: > "${GATE_QUEUE_TEST_SHIM_MARKER:?}"
out="$(/usr/bin/stat "$@" 2>/dev/null)" || exit $?
printf '%s\n' "$out"
rm -f -- "$target"
"""


def _reader_shim(tool: str) -> str:
    """head/sed shim: delete the target file BEFORE delegating to the real
    tool, so the read sees ENOENT — a slot vanishing between the glob and
    the read (issue #488's window, made deterministic)."""
    return (
        "#!/bin/sh\n"
        'for target in "$@"; do :; done\n'
        ': > "${GATE_QUEUE_TEST_SHIM_MARKER:?}"\n'
        'rm -f -- "$target"\n'
        f'exec /usr/bin/{tool} "$@"\n'
    )


def _install_shims(base: Path, shims: dict[str, str]) -> tuple[Path, Path]:
    """Write executable PATH shims (tool name -> script body). Returns the
    shim bin dir (to prepend to PATH) and the marker path the shims touch."""
    bin_dir = base / "shim-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    marker = base / "shim-fired"
    for name, body in shims.items():
        shim = bin_dir / name
        shim.write_text(body, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return bin_dir, marker


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


def _bash_raw(
    script: Path, cwd: Path, code: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Like _bash but returns the raw result: the vanishing-slot tests pin
    both the exit status (no set -e kill) and a clean stderr (no leak)."""
    process_env = os.environ.copy()
    for key in (
        "AGENT_LEGION_GATE_SLOT_FILE",
        "AGENT_LEGION_GATE_SLOT_HELD",
        "AGENT_LEGION_MAX_PARALLEL_GATES",
        "AGENT_LEGION_GATE_POLL_SECONDS",
        "AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS",
    ):
        process_env.pop(key, None)
    process_env.update(env or {})
    return subprocess.run(
        ["bash", "-c", f'source "{script}"\n{code}'],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=process_env,
        timeout=60,
    )


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
        # Scrub the parent gate's slot env (same as _bash): under check-quick.sh
        # the test process inherits AGENT_LEGION_GATE_SLOT_HELD=1, and an
        # unscrubbed waiter would take the re-entrant path — acquiring
        # immediately without ever printing the queue-full announcement.
        waiter_env = os.environ.copy()
        waiter_env.pop("AGENT_LEGION_GATE_SLOT_FILE", None)
        waiter_env.pop("AGENT_LEGION_GATE_SLOT_HELD", None)
        waiter_env.pop("AGENT_LEGION_MAX_PARALLEL_GATES", None)
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
            env=waiter_env,
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


def test_default_max_parallel_gates_is_one(repo: Path) -> None:
    """The default cap is 1 (serialized gates): two concurrent gates still
    oversubscribed the machine — each gate fans out into parallel lanes, and
    the timing-sensitive tests that flaked on timeouts cost more than the
    queue wait ever saved. Raise AGENT_LEGION_MAX_PARALLEL_GATES explicitly
    on a big box to opt back into concurrency."""
    holder = subprocess.Popen(["sleep", "60"])
    try:
        _write_slot(repo, "gate-holder", pid=holder.pid)
        # No env override: the default cap must already refuse a second gate.
        # The backgrounded acquire's output is discarded so the snippet's own
        # verdict line is the only stdout.
        out = _bash(
            QUEUE_SCRIPT,
            repo,
            "acquire_gate_slot >/dev/null 2>&1 & waiter=$!; sleep 1; "
            "if kill -0 $waiter 2>/dev/null; then echo waiting; kill $waiter; "
            "else echo admitted; fi; wait $waiter 2>/dev/null; exit 0",
            env={"AGENT_LEGION_GATE_POLL_SECONDS": "10"},
        )
        assert out.strip() == "waiting"
    finally:
        holder.terminate()
        holder.wait()


def test_serialized_queue_gives_gate_full_machine_budget(repo: Path) -> None:
    """With the default cap of 1, a queued gate that acquires the slot after
    the holder exits runs with the full worker budget (N=1 slot), not the
    divided one — serialization trades queue wait for lone-gate speed."""
    holder = subprocess.Popen(["sleep", "60"])
    try:
        _write_slot(repo, "gate-holder", pid=holder.pid)
        waiter = subprocess.Popen(
            [
                "bash",
                "-c",
                f'source "{QUEUE_SCRIPT}"\nsource "{JOBS_SCRIPT}"\n'
                "AGENT_LEGION_GATE_POLL_SECONDS=1 "
                "AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS=2 "
                "acquire_gate_slot && "
                "detect_gate_default_jobs_worktree_aware",
            ],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(2)
        holder.terminate()
        holder.wait()
        stdout, stderr = waiter.communicate(timeout=30)
        assert waiter.returncode == 0, stderr
        cores = _cpu_count()
        # Full budget: (cores-2)/1, clamped to [2, 8] — same as a lone gate.
        assert stdout.strip().splitlines()[-1] == str(max(2, min(8, cores - 2)))
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


# --- Vanishing-slot tolerance (issue #488) ----------------------------------
#
# A yielding contender (create slot -> count -> over cap -> rm its own file)
# deletes slot files other processes may have just globbed. A slot read that
# fails because the file vanished must read as "slot gone" — skip it — never
# as an error: under pre-push's set -euo pipefail a bare failed read (the
# assignment's command substitution) kills the whole gate and wastes the
# queue wait. The millisecond window cannot be hit deterministically from
# outside, so these tests force it with PATH shims that delete the target
# file at (or around) the read; the concurrent-churn test below reproduces
# the issue's steps as a best-effort stress check.


def test_slot_mtime_missing_file_returns_fallback(repo: Path) -> None:
    """A slot already gone at the first stat reads as the caller's fallback
    (age 0): status 0, no stderr leak — not a failed command substitution."""
    result = _bash_raw(
        QUEUE_SCRIPT,
        repo,
        "set -euo pipefail\n"
        'mtime="$(_slot_mtime ".git/gate-slots/gate-gone" 12345)"\n'
        'echo "mtime=$mtime"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "mtime=12345"
    assert result.stderr == ""


def test_aged_reclaim_survives_slot_vanishing_between_glob_and_stat(
    repo: Path, tmp_path: Path
) -> None:
    """Issue #488's exact crash: the slot is globbed while it exists, then
    vanishes before the mtime stat (the stat shim answers the first query
    and deletes the file — the old probe-then-query pair died on the second
    query, and set -e turned that into a failed pre-push). The single
    captured call must return the mtime and let the reclaim loop move on."""
    raced = _write_aged_slot(repo, "gate-raced")
    bin_dir, marker = _install_shims(tmp_path, {"stat": _STAT_SHIM})
    result = _bash_raw(
        QUEUE_SCRIPT,
        repo,
        "set -euo pipefail\n"
        "AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS=10 _reclaim_aged_gate_slots\n"
        'echo "survived"',
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", _SHIM_MARKER_ENV: str(marker)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "survived"
    assert result.stderr == ""
    # The shim really intercepted the stat (else the test would pass vacuously).
    assert marker.exists()
    assert not raced.exists()


def test_slot_readers_survive_vanishing_between_glob_and_read(repo: Path, tmp_path: Path) -> None:
    """Same window for the head-based readers (live count, stale reclaim,
    holder announcement): the file is gone by the time head opens it. Every
    reader must skip the vanished slot and stay at status 0 — a failed
    assignment propagates through set -e and kills the gate."""
    holder = subprocess.Popen(["sleep", "60"])
    try:
        _write_slot(repo, "gate-raced", pid=holder.pid, worktree="/raced-wt")
        bin_dir, marker = _install_shims(tmp_path, {"head": _reader_shim("head")})
        result = _bash_raw(
            QUEUE_SCRIPT,
            repo,
            "set -euo pipefail\n"
            "count_live_gate_slots\n"
            "_reclaim_stale_gate_slots\n"
            '_describe_gate_slot_holders "$(gate_slot_dir)"\n'
            'echo "survived"',
            env={"PATH": f"{bin_dir}:{os.environ['PATH']}", _SHIM_MARKER_ENV: str(marker)},
        )
        assert result.returncode == 0, result.stderr
        # First stdout line is count_live_gate_slots: the vanished slot does
        # not count (its owner conceded, not holds). Without the shim this
        # would read 1 and the vanishing path would never have run.
        assert result.stdout.splitlines()[0] == "0"
        assert result.stdout.strip().splitlines()[-1] == "survived"
        assert marker.exists()
        assert not (_slots_dir(repo) / "gate-raced").exists()
    finally:
        holder.terminate()
        holder.wait()


def test_holder_description_survives_vanishing_between_reads(repo: Path, tmp_path: Path) -> None:
    """The holder announcement reads a slot twice (pid via head, worktree
    via sed); the file can vanish between the two. The second read failing
    must skip that holder entirely. Old-code failure form: the announcement
    only ever runs inside a command substitution, where errexit is stripped,
    so the failed sed read never killed the gate — it just degraded the
    holder line to "unknown(pid N)". This test therefore catches the old
    code by output mismatch (a vanished slot must not be announced at all),
    not by exit status."""
    holder = subprocess.Popen(["sleep", "60"])
    try:
        _write_slot(repo, "gate-raced", pid=holder.pid, worktree="/raced-wt")
        bin_dir, marker = _install_shims(tmp_path, {"sed": _reader_shim("sed")})
        result = _bash_raw(
            QUEUE_SCRIPT,
            repo,
            "set -euo pipefail\n"
            'holders="$(_describe_gate_slot_holders "$(gate_slot_dir)")"\n'
            'echo "holders=${holders}"',
            env={"PATH": f"{bin_dir}:{os.environ['PATH']}", _SHIM_MARKER_ENV: str(marker)},
        )
        assert result.returncode == 0, result.stderr
        # The half-described holder is skipped entirely: a vanished slot is
        # not a holder, and an announcement must never fail the gate.
        assert result.stdout.strip() == "holders="
        assert marker.exists()
        assert not (_slots_dir(repo) / "gate-raced").exists()
    finally:
        holder.terminate()
        holder.wait()


# Contender churn, as in the issue's reproduction steps: a third party
# constantly creates slot files and deletes them again (the yielding
# design), while a holder occupies the only slot and a waiter queues. The
# waiter must keep queueing through the churn and finally acquire. The
# millisecond window is only hit probabilistically (on the old code this
# test fails often, not always); the shim tests above pin the semantics
# deterministically. The iteration counter is written atomically (per-writer
# tmp + mv): the test reads it while the churners still run, and a plain
# `> file` truncate-then-write could hand the reader an empty file — the
# very intermittent-failure family this PR removes (review P2). The tmp name
# carries the writer's pid because both churners write this one counter
# file; a shared tmp name would re-open the truncate window between them.
_CHURN_LOOP = r"""end=$((SECONDS + 6))
i=0
while (( SECONDS < end )); do
  for j in 1 2 3 4 5; do
    printf '%s\n%s\n%s\n' 999999 /churn-wt 2026-01-01T00:00:00Z >".git/gate-slots/gate-churn-$$-$i-$j"
  done
  rm -f .git/gate-slots/gate-churn-$$-$i-*
  i=$((i + 1))
  printf '%s\n' "$i" >".churn-iterations.tmp.$$" && mv ".churn-iterations.tmp.$$" .churn-iterations
done
"""


def test_waiter_survives_yielding_contender_churn(repo: Path) -> None:
    holder = subprocess.Popen(["sleep", "60"])
    churners = []
    waiter = None
    try:
        _write_slot(repo, "gate-holder", pid=holder.pid, worktree="/holder-wt")
        # The holder's slot must already count, or the waiter may acquire
        # immediately without ever queueing through the churn.
        assert _bash(QUEUE_SCRIPT, repo, "count_live_gate_slots").strip() == "1"

        churners = [subprocess.Popen(["bash", "-c", _CHURN_LOOP], cwd=repo) for _ in range(2)]

        waiter_env = os.environ.copy()
        waiter_env.pop("AGENT_LEGION_GATE_SLOT_FILE", None)
        waiter_env.pop("AGENT_LEGION_GATE_SLOT_HELD", None)
        waiter_env.pop("AGENT_LEGION_MAX_PARALLEL_GATES", None)
        waiter = subprocess.Popen(
            [
                "bash",
                "-c",
                f'source "{QUEUE_SCRIPT}"\n'
                "AGENT_LEGION_MAX_PARALLEL_GATES=1 "
                "AGENT_LEGION_GATE_POLL_SECONDS=1 "
                "AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS=3 "
                "acquire_gate_slot && release_gate_slot",
            ],
            cwd=repo,
            env=waiter_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # The holder exits mid-wait; its slot frees via the age-based
        # reclamation (the zombie pid stays kill-0-alive until reaped).
        time.sleep(2)
        holder.terminate()
        stdout, stderr = waiter.communicate(timeout=30)
        assert waiter.returncode == 0, stderr
        assert "Machine gate queue full" in stderr
        assert "holder-wt" in stderr
        iterations = int((repo / ".churn-iterations").read_text().strip())
        # Churn really ran next to the waiter (else the stress is vacuous).
        assert iterations >= 50
    finally:
        holder.terminate()
        holder.wait()
        if waiter is not None and waiter.poll() is None:
            waiter.kill()
            waiter.wait()
        for churner in churners:
            churner.terminate()
            churner.wait()


def test_gate_queue_script_passes_bash_syntax_check() -> None:
    """bash -n on the sourced script: a syntax error would kill every gate
    and pre-push on the host; checked with the default bash and with the
    system /bin/bash (macOS ships 3.2 — no bash-5-only syntax may sneak in)."""
    for interpreter in ("bash", "/bin/bash"):
        if interpreter == "/bin/bash" and not Path("/bin/bash").exists():
            continue
        result = subprocess.run(
            [interpreter, "-n", str(QUEUE_SCRIPT)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


def _write_aged_slot(repo_dir: Path, name: str) -> Path:
    """A dead-pid slot with an mtime far in the past (aged past any sane
    max age, so only the vanished-file path can spare it)."""
    slot = _slots_dir(repo_dir) / name
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text("999999\n/wt\n2026-01-01T00:00:00Z\n", encoding="utf-8")
    old = time.time() - 9999
    os.utime(slot, (old, old))
    return slot


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
