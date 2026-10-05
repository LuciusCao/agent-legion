"""Pre-push transport-death regression tests (issue #679, second round).

The first #679 fix (`trap '' PIPE` across the hook chain) could not stop the
symptom, because a SIGPIPE inside the hook chain never surfaces as `git push`
exit 141: git reports a failed hook as "failed to push" with exit 1. A push
that exits 141 after a passing gate is git itself dying on one of its OWN
pipes (git resets SIGPIPE to the default at startup, so no trap reaches it).
The writer that also leaves the remote ref unmoved is git sending the ref
updates to its remote helper (git-remote-https for this repo's origin),
spawned BEFORE the hook ran and dead by the time the multi-minute gate ends.

These tests drive real `git push` runs over the real hook chain
(.githooks/pre-push -> scripts/run-local-gate.sh) with a stub gate and a
push-only stub remote helper shaped like git-remote-https (`push`
capability, `git remote-<name>` wrapper + helper child processes).
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Push-only remote helper: the same protocol surface git-remote-https offers
# for a push (capabilities / list for-push / push batch), delivering refs into
# a local bare repo with send-pack (no hooks on the helper side).
STUB_REMOTE_HELPER = """#!/usr/bin/env bash
remote="$2"
pending=()
while IFS= read -r line; do
  case "$line" in
    capabilities) printf 'push\\n\\n' ;;
    list|"list for-push")
      git --git-dir="$remote" for-each-ref --format='%(objectname) %(refname)'
      printf '\\n' ;;
    push\\ *) pending+=("${line#push }") ;;
    "")
      [[ ${#pending[@]} -eq 0 ]] && exit 0
      for spec in "${pending[@]}"; do
        if git send-pack --force "$remote" "${spec#+}" >/dev/null 2>&1; then
          printf 'ok %s\\n' "${spec#*:}"
        else
          printf 'error %s send-pack failed\\n' "${spec#*:}"
        fi
      done
      printf '\\n'
      pending=() ;;
    *) printf 'unsupported\\n' ;;
  esac
done
"""

# Stub gate: resolves git push's pid from its own ancestry (gate -> hook ->
# git) — independent of anything the fixed hook exports, so the same stub
# reproduces the failure on the unfixed scripts — then kills git's transport
# children (helper wrapper and the helper below it): the connection dies while
# the gate runs. The gate itself passes.
KILL_TRANSPORT_GATE = """#!/usr/bin/env bash
set -euo pipefail
hook_pid=$PPID
git_pid="$(ps -o ppid= -p "$hook_pid" | tr -d ' ')"
children_of() {
  ps -A -o pid= -o ppid= | awk -v p="$1" -v h="$hook_pid" '$2 == p && $1 != h { print $1 }'
}
for child in $(children_of "$git_pid"); do
  for grandchild in $(children_of "$child"); do
    kill "$grandchild" 2>/dev/null || true
  done
  kill "$child" 2>/dev/null || true
done
sleep 0.3
echo "stub gate passed"
"""


def _env(bin_dir: Path | None = None) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    for name in (
        "AGENT_LEGION_PRE_PUSH_GIT_PID",
        "AGENT_LEGION_GATE_LEVEL",
        "GATE_TIER",
        "GATE_LANES",
    ):
        env.pop(name, None)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run(
    args: list[str | Path], cwd: Path, *, bin_dir: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=_env(bin_dir),
        text=True,
        capture_output=True,
        check=check,
    )


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fixture(tmp_path: Path, gate_script: str) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / ".githooks" / "pre-push", repo / ".githooks" / "pre-push")
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "run-local-gate.sh", repo / "scripts" / "run-local-gate.sh"
    )
    # The hook sources the shared lane path rules (#941).
    shutil.copy2(PROJECT_ROOT / "scripts" / "lane-paths.sh", repo / "scripts" / "lane-paths.sh")
    _write_executable(repo / "scripts" / "check-quick.sh", gate_script)
    _write_executable(tmp_path / "bin" / "git-remote-stubpush", STUB_REMOTE_HELPER)
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.email", "transport@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Transport Test"], cwd=repo)
    _run(["git", "config", "core.hooksPath", ".githooks"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-qm", "fixture"], cwd=repo)
    remote = tmp_path / "remote.git"
    _run(["git", "init", "-q", "--bare", remote], cwd=tmp_path)
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    return repo, remote, head


def _remote_ref(remote: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "-q", ref],
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _diagnostics(repo: Path) -> str:
    path = repo / ".git" / "local-gates" / "push-diagnostics.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_helper_death_during_gate_fails_explicitly_not_sigpipe(tmp_path: Path) -> None:
    # The issue's exact symptom set: gate passed, evidence written, `git push`
    # exit 141, remote ref not moved, retry passes on cached evidence. Pre-fix
    # the hook returned 0 into a dead remote helper and git died of SIGPIPE
    # writing the ref updates to it.
    repo, remote, head = _fixture(tmp_path, KILL_TRANSPORT_GATE)
    bin_dir = tmp_path / "bin"
    url = f"stubpush::{remote}"

    first = _run(
        ["git", "push", url, "HEAD:refs/heads/feature"], cwd=repo, bin_dir=bin_dir, check=False
    )

    assert first.returncode not in (-signal.SIGPIPE, 128 + signal.SIGPIPE), (
        f"git push died of SIGPIPE after a passing gate (issue #679): {first.stderr}"
    )
    assert first.returncode == 1, first.stderr
    assert "push connection" in first.stderr
    assert "re-run git push" in first.stderr
    assert _remote_ref(remote, "refs/heads/feature") is None
    evidence = list((repo / ".git" / "local-gates" / head).glob("quick-*.pass"))
    assert evidence, "the gate passed: its evidence must be cached for the retry"
    diagnostics = _diagnostics(repo)
    assert "transport pid(s)" in diagnostics
    assert f"head={head[:12]}" in diagnostics

    retry = _run(
        ["git", "push", url, "HEAD:refs/heads/feature"], cwd=repo, bin_dir=bin_dir, check=False
    )

    assert retry.returncode == 0, retry.stderr
    assert "reusing cached evidence" in retry.stdout + retry.stderr
    assert _remote_ref(remote, "refs/heads/feature") == head


def test_gate_signal_death_is_attributed(tmp_path: Path) -> None:
    # A gate killed by a signal fails the push (git exit 1) and leaves a record
    # naming the stage and signal instead of a bare exit code.
    repo, remote, _ = _fixture(tmp_path, "#!/usr/bin/env bash\nkill -TERM $$\n")

    result = _run(["git", "push", remote, "HEAD:refs/heads/feature"], cwd=repo, check=False)

    assert result.returncode == 1, result.stderr
    assert _remote_ref(remote, "refs/heads/feature") is None
    assert "died with status 143 (signal 15)" in _diagnostics(repo)
    assert "died with status 143" in result.stderr


def test_output_reader_gone_is_recorded_and_push_lands(tmp_path: Path) -> None:
    # The other git-owned SIGPIPE writer: the push's output reader leaving
    # while the gate runs. Nothing in the hook can save git's own status
    # writes (git may still exit 141 printing them), but the hook must not
    # fail the push for it — the ref lands — and must record the cause so a
    # following 141 is attributable.
    repo, remote, head = _fixture(
        tmp_path, "#!/usr/bin/env bash\nsleep 0.5\necho 'stub gate passed' || true\n"
    )
    with subprocess.Popen(
        ["git", "push", str(remote), "HEAD:refs/heads/feature"],
        cwd=repo,
        env=_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as process:
        assert process.stdout is not None
        process.stdout.close()  # the reader walks away before the gate ends
        process.wait(timeout=60)

    assert _remote_ref(remote, "refs/heads/feature") == head
    assert "push output reader went away" in _diagnostics(repo)
