"""Pre-push SIGPIPE regression tests (issue #679).

Mechanism under test: `git push` runs the pre-push hook on the pusher's own
stdout/stderr. Under an agent harness (or `git push | head`-style pipelines)
those are pipes whose reader may go away while the hook is still writing —
output caps, truncation, a killed session. The quick gate's post-pass output
stage used to cat full per-lane logs through that pipe (pytest/vitest/cargo
output easily reaches megabytes), so a reader that left turned into SIGPIPE
(141) for the gate chain: git treats a dead hook as a failed one and aborts
the push, so the remote ref never moves even though the gate had passed. The
retry then replays the cached gate evidence — the "push twice and it works"
ritual.

The fix (mirrored across .githooks/pre-push, scripts/run-local-gate.sh and
scripts/check-quick.sh / check.sh): ignore SIGPIPE, guard every write, and
cap the lane-log cat so the hook's total output stays small — a capped
reader never has a reason to walk away in the first place.

These tests drive the consumer side of the pipe (read a slice of the hook's
output, then close the read end early) against the real hook chain
(.githooks/pre-push → scripts/run-local-gate.sh → scripts/check-quick.sh)
with stub lane scripts standing in for the toolchain. They fail on the
unfixed scripts (hook/gate death, aborted push) and pass on the fixed ones.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZERO_SHA = "0" * 40

# Firehose volume for the stub lanes: comfortably past any pipe buffer
# (64KiB) and past any sane output cap, while keeping the stubs fast.
FIREHOSE_LINES = 5000


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(
    args: list[str | Path],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    for name in tuple(process_env):
        if name.startswith("GIT_"):
            process_env.pop(name)
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=process_env,
        text=True,
        capture_output=True,
        check=check,
    )


def _init_repo(repo: Path) -> None:
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "sigpipe@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "SIGPIPE Test"], cwd=repo)
    (repo / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-qm", "fixture"], cwd=repo)


def _closing_reader(
    args: list[str | Path],
    *,
    cwd: Path,
    env: dict[str, str],
    read_bytes: int,
    stdin_text: str | None = None,
) -> tuple[int, str]:
    """Run a command whose stdout is a pipe, read a slice, then close early.

    This is the agent-harness / capped-consumer behavior from issue #679: the
    reader stops consuming and closes the read end while the writer is still
    producing output. stderr is a separate live pipe so the command's own
    diagnostics survive (isolating the hook's fate from git's).
    """
    process_env = os.environ.copy()
    for name in tuple(process_env):
        if name.startswith("GIT_"):
            process_env.pop(name)
    process_env.update(env)
    stdin = subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL
    with subprocess.Popen(
        [str(arg) for arg in args],
        cwd=cwd,
        env=process_env,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout is not None
        if stdin_text is not None:
            assert process.stdin is not None
            process.stdin.write(stdin_text)
            process.stdin.close()
        process.stdout.read(read_bytes)
        process.stdout.close()  # the reader walks away mid-output
        stderr = process.stderr.read() if process.stderr else ""
        returncode = process.wait()
    return returncode, stderr


def _sigpipe_death(returncode: int) -> bool:
    return returncode in (-signal.SIGPIPE, 128 + signal.SIGPIPE)


@pytest.fixture
def hook_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Fixture repo with the REAL pre-push hook chain and a stub gate.

    The stub gate models the fixed gate contract from issue #679: SIGPIPE
    ignored, every write guarded, output bounded — it keeps writing after the
    reader left (proving the immunity) without producing an unbounded
    firehose (which no fixed gate does anymore).
    """
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / ".githooks" / "pre-push", repo / ".githooks" / "pre-push")
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "run-local-gate.sh",
        repo / "scripts" / "run-local-gate.sh",
    )
    passed_marker = tmp_path / "gate-passed"
    _write_executable(
        repo / "scripts" / "check-quick.sh",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "trap '' PIPE\n"
        # Evidence-like marker first: the gate has finished its checks before
        # the output stage starts, exactly like the real gate whose evidence
        # run-local-gate.sh records after this script exits 0.
        'touch "$GATE_PASSED_MARKER"\n'
        # ...then the output stage, still writing after any reader left.
        "for i in $(seq 1 2000); do\n"
        '  printf "gate output line %s ........................................\\n" "$i" || true\n'
        "done\n",
    )
    _init_repo(repo)
    _run(["git", "config", "core.hooksPath", ".githooks"], cwd=repo)
    passed_marker.unlink(missing_ok=True)
    return repo, passed_marker


def test_pre_push_hook_survives_closed_output_pipe(hook_repo: tuple[Path, Path]) -> None:
    # The hook chain must exit 0 even when its stdout reader disappears during
    # the gate's output stage: the push verdict may only come from the gate's
    # exit status, never from whether the chatter could be drained. Pre-fix,
    # the hook died of SIGPIPE (141) here and git aborted the push.
    repo, passed_marker = hook_repo
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    push_input = f"refs/heads/local {head} refs/heads/feature/test {ZERO_SHA}\n"

    returncode, stderr = _closing_reader(
        [repo / ".githooks" / "pre-push"],
        cwd=repo,
        env={"GATE_PASSED_MARKER": str(passed_marker)},
        read_bytes=512,
        stdin_text=push_input,
    )

    assert returncode == 0, (
        f"pre-push hook exited {returncode} "
        f"({'SIGPIPE death' if _sigpipe_death(returncode) else 'see stderr'}): {stderr}"
    )
    assert passed_marker.exists(), "the gate never finished: the hook died mid-run"


def test_git_push_survives_harness_output_cap_and_updates_ref(
    tmp_path: Path,
) -> None:
    # End to end over the REAL chain (.githooks/pre-push →
    # scripts/run-local-gate.sh → scripts/check-quick.sh) with stub lanes: an
    # agent-style consumer (reads up to 64KiB, then closes if there is more)
    # must not break the push. Pre-fix, the lane-log cats blew past the cap,
    # the gate died of SIGPIPE, and git aborted the push with the remote ref
    # never moving; post-fix the hook's total output stays under the cap, so
    # the reader never leaves and the push lands.
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    (repo / ".githooks").mkdir(parents=True)
    scripts.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / ".githooks" / "pre-push", repo / ".githooks" / "pre-push")
    for name in (
        "run-local-gate.sh",
        "check-quick.sh",
        "check-quick-backend.sh",
        "check-quick-frontend.sh",
        "gate-jobs.sh",
        "gate-queue.sh",
    ):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, scripts / name)
    # Stub lanes firehose into the log files the gate gives them (their fd 1
    # is the lane log, like pytest/vitest/cargo under the real gate) — only
    # the gate's capped cat decides what reaches the hook's stdout.
    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        f"for i in $(seq 1 {FIREHOSE_LINES}); do\n"
        '  printf "backend lane line %s ........................................\\n" "$i"\n'
        "done\n",
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "${FRONTEND_GATE_PHASE:-}" == "api-contract" ]]; then\n'
        f"  for i in $(seq 1 {FIREHOSE_LINES}); do\n"
        '    printf "api contract line %s ....................................\\n" "$i"\n'
        "  done\n"
        "fi\n",
    )
    _init_repo(repo)
    _run(["git", "config", "core.hooksPath", ".githooks"], cwd=repo)

    remote = tmp_path / "remote.git"
    _run(["git", "init", "-q", "--bare", remote], cwd=tmp_path)
    _run(["git", "remote", "add", "origin", remote], cwd=repo)
    _run(["git", "commit", "-q", "--allow-empty", "-m", "change"], cwd=repo)
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    returncode, stderr = _closing_reader(
        ["git", "push", "origin", "HEAD:refs/heads/feature/sigpipe"],
        cwd=repo,
        env={},
        read_bytes=64 * 1024,
    )

    assert returncode == 0, (
        f"git push exited {returncode} "
        f"({'SIGPIPE death' if _sigpipe_death(returncode) else 'see stderr'}): {stderr}"
    )
    pushed = _run(
        ["git", "--git-dir", remote, "rev-parse", "refs/heads/feature/sigpipe"],
        cwd=tmp_path,
        check=False,
    )
    assert pushed.returncode == 0, "remote ref missing: the push was aborted"
    assert pushed.stdout.strip() == head
    # The gate evidence run-local-gate.sh recorded is what makes the retry of
    # the pre-fix failure instant — it must exist after the successful push.
    evidence = list((repo / ".git" / "local-gates" / head).glob("quick-*.pass"))
    assert evidence, "no gate evidence recorded for the pushed SHA"


def _gate_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    for name in (
        # Mirror the scrubbing in test_quality_gate_scripts.py: nothing may
        # leak in from an outer gate run.
        "AGENT_LEGION_TEST_DATABASE_URL",
        "AGENT_LEGION_COV",
        "AGENT_LEGION_FRONTEND_TEST_WORKERS",
        "AGENT_LEGION_RUST_WORKERS",
        "AGENT_LEGION_TEST_WORKERS",
        "AGENT_LEGION_GATE_OUTPUT_LINES",
        "BACKEND_GATE_PHASE",
        "BACKEND_SKIP_WORKER_UI_TESTS",
        "COVERAGE_FILE",
        "FRONTEND_API_CHECK",
        "FRONTEND_COVERAGE_BLOB_DIR",
        "FRONTEND_GATE_PHASE",
        "FRONTEND_TEST_MODE",
        "FRONTEND_TEST_PROJECT",
        "GATE_LANES",
        "GATE_SHARD",
        "GATE_SKIP_STATIC",
        "GATE_TIER",
        "KEEP_COVERAGE",
    ):
        env.pop(name, None)
    if extra:
        env.update(extra)
    return env


def _quick_gate_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Fixture running the REAL check-quick.sh with firehosing stub lanes."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "check-quick.sh", scripts / "check-quick.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-jobs.sh", scripts / "gate-jobs.sh")
    shutil.copy2(PROJECT_ROOT / "scripts" / "gate-queue.sh", scripts / "gate-queue.sh")
    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        f"for i in $(seq 1 {FIREHOSE_LINES}); do\n"
        '  printf "backend lane line %s ........................................\\n" "$i"\n'
        "done\n",
    )
    _write_executable(
        scripts / "check-quick-frontend.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "${FRONTEND_GATE_PHASE:-}" == "api-contract" ]]; then\n'
        f"  for i in $(seq 1 {FIREHOSE_LINES}); do\n"
        '    printf "api contract line %s ....................................\\n" "$i"\n'
        "  done\n"
        "fi\n",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    return repo, scripts


def test_check_quick_bounds_lane_output_on_stdout(tmp_path: Path) -> None:
    # The firehose itself is capped: a passing lane's full log no longer
    # streams through the hook's stdout — only the tail (where pytest/vitest/
    # cargo summaries live), the same for the api-contract integration step.
    # Pre-fix, all 5000 lines per lane were cat-ed straight through.
    repo, scripts = _quick_gate_fixture(tmp_path)

    result = _run(
        [scripts / "check-quick.sh"],
        cwd=repo,
        env=_gate_env(),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Parallel quick gate passed" in result.stdout
    assert "backend lane line 5000 " in result.stdout  # the tail survives
    assert "backend lane line 100 " not in result.stdout  # the head does not
    assert "last 120 of 5000 lines" in result.stdout  # the cap announces itself
    assert "api contract line 5000 " in result.stdout
    assert "api contract line 100 " not in result.stdout
    # Total emitted volume stays bounded (capped lanes + capped api-contract
    # + chatter) instead of the ~15k lines the firehose used to produce.
    assert len(result.stdout.splitlines()) < 800


def test_check_quick_output_cap_override_and_zero(tmp_path: Path) -> None:
    # AGENT_LEGION_GATE_OUTPUT_LINES tunes the cap; 0 restores the full cat
    # for debugging.
    repo, scripts = _quick_gate_fixture(tmp_path)

    capped = _run(
        [scripts / "check-quick.sh"],
        cwd=repo,
        env=_gate_env({"AGENT_LEGION_GATE_OUTPUT_LINES": "10"}),
    )
    assert capped.returncode == 0, capped.stdout + capped.stderr
    assert "last 10 of 5000 lines" in capped.stdout
    assert "backend lane line 4991 " in capped.stdout
    assert "backend lane line 4990 " not in capped.stdout

    full = _run(
        [scripts / "check-quick.sh"],
        cwd=repo,
        env=_gate_env({"AGENT_LEGION_GATE_OUTPUT_LINES": "0"}),
    )
    assert full.returncode == 0, full.stdout + full.stderr
    assert "backend lane line 1 " in full.stdout


def test_check_quick_keeps_lane_logs_on_failure(tmp_path: Path) -> None:
    # A failing gate keeps its full lane logs on disk (only the bounded tail
    # reached stdout) and says where they are; a passing gate's logs are
    # ephemeral.
    repo, scripts = _quick_gate_fixture(tmp_path)
    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        "for i in $(seq 1 3000); do\n"
        '  printf "failing lane line %s ......................................\\n" "$i"\n'
        "done\n"
        "exit 7\n",
    )

    result = _run(
        [scripts / "check-quick.sh"],
        cwd=repo,
        env=_gate_env(),
        check=False,
    )

    assert result.returncode == 1
    assert "backend=7" in result.stderr
    kept = [line for line in result.stderr.splitlines() if "Full lane logs kept" in line]
    assert kept, f"no kept-logs announcement in stderr:\n{result.stderr}"
    log_dir = Path(kept[0].rsplit(":", 1)[1].strip())
    assert log_dir.is_dir(), f"announced log dir {log_dir} does not exist"
    backend_log = log_dir / "backend-static-check.log"
    assert backend_log.is_file()
    assert len(backend_log.read_text(encoding="utf-8").splitlines()) == 3000


def test_check_quick_api_contract_failure_keeps_logs_and_announces(
    tmp_path: Path,
) -> None:
    # The api-contract integration step used to fail through set -e with its
    # output sitting in the log file the EXIT trap then deleted — a push
    # rejected with exit 5 and zero diagnostics anywhere. The failure must
    # print the capped tail, announce itself on stderr, and keep the full log.
    repo, scripts = _quick_gate_fixture(tmp_path)
    _write_executable(
        scripts / "check-quick-frontend.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "${FRONTEND_GATE_PHASE:-}" == "api-contract" ]]; then\n'
        "  for i in $(seq 1 200); do\n"
        '    printf "contract mismatch detail %s\\n" "$i"\n'
        "  done\n"
        "  exit 5\n"
        "fi\n"
        "exit 0\n",
    )

    result = _run(
        [scripts / "check-quick.sh"],
        cwd=repo,
        env=_gate_env(),
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "API contract check failed (status=5)" in result.stderr
    # The capped tail reached stdout before the failure announcement: last 120
    # of 200 keeps line 200 and cuts line 50.
    assert "last 120 of 200 lines" in result.stdout
    assert "contract mismatch detail 200" in result.stdout
    assert "contract mismatch detail 50" not in result.stdout
    kept = [line for line in result.stderr.splitlines() if "Full lane logs kept" in line]
    assert kept, f"no kept-logs announcement in stderr:\n{result.stderr}"
    log_dir = Path(kept[0].rsplit(":", 1)[1].strip())
    api_log = log_dir / "api-contract.log"
    assert api_log.is_file(), f"api-contract log missing in kept dir {log_dir}"
    assert len(api_log.read_text(encoding="utf-8").splitlines()) == 200


def test_check_quick_passing_run_leaves_no_logs_behind(tmp_path: Path) -> None:
    # The mirror of the failure case: a passing gate's tmp logs are removed,
    # so bounded output cannot slowly fill /tmp across daily pushes. The gate
    # runs against an ISOLATED TMPDIR: the shared runner TMPDIR is scanned by
    # sibling xdist workers whose own agent-legion-quick.* mktemp dirs would
    # otherwise race this assertion (CI backend-unit flake, 2026-09-16).
    repo, scripts = _quick_gate_fixture(tmp_path)
    isolated_tmp = tmp_path / "gate-tmp"
    isolated_tmp.mkdir()
    before = {p.name for p in isolated_tmp.iterdir()}

    gate_env = _gate_env()
    gate_env["TMPDIR"] = str(isolated_tmp)
    result = _run(
        [scripts / "check-quick.sh"],
        cwd=repo,
        env=gate_env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    after = {p.name for p in isolated_tmp.iterdir()}
    leaked = {name for name in after - before if name.startswith("agent-legion-quick.")}
    assert not leaked, f"quick-gate log dirs leaked: {sorted(leaked)}"


def test_check_quick_survives_closed_output_pipe(tmp_path: Path) -> None:
    # Direct invocation (manual runs, scripts/check.sh segments): the gate's
    # verdict must survive a stdout reader that walks away mid-run. Pre-fix,
    # the first write after the reader left — a round marker, a heartbeat, the
    # lane-output cat — killed the script with SIGPIPE.
    repo, scripts = _quick_gate_fixture(tmp_path)
    _write_executable(
        scripts / "check-quick-backend.sh",
        "#!/usr/bin/env bash\n"
        "sleep 0.3\n"
        f"for i in $(seq 1 {FIREHOSE_LINES}); do\n"
        '  printf "late lane line %s ..........................................\\n" "$i"\n'
        "done\n",
    )

    returncode, stderr = _closing_reader(
        [scripts / "check-quick.sh"],
        cwd=repo,
        env=_gate_env(),
        read_bytes=16,
    )

    assert returncode == 0, (
        f"check-quick.sh exited {returncode} "
        f"({'SIGPIPE death' if _sigpipe_death(returncode) else 'see stderr'}): {stderr}"
    )
