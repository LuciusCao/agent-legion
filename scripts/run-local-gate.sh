#!/usr/bin/env bash
set -euo pipefail

# SIGPIPE immunity (issue #679): .githooks/pre-push execs this script, so it
# IS the pre-push hook process and its stdout is git push's own stdout. When
# that stream is a pipe whose reader walks away mid-push (agent harness output
# caps, `git push | tail`, a killed session), any write to the dead pipe
# otherwise kills the gate with SIGPIPE (141) — git then aborts the push even
# though the gate had already passed and its evidence was recorded, which is
# exactly the "push twice, second one replays the cached evidence" symptom.
# Ignored here rather than only in .githooks/pre-push so manual runs
# (scripts/check.sh segments, terminal pipes) get the same immunity, and so
# the gate scripts below cannot reintroduce the death by resetting traps.
# Guards around every write still keep set -e clean after EPIPE.
trap '' PIPE
# A failed write means the push's output reader is gone (EPIPE, SIGPIPE being
# ignored); remembered for the post-gate push diagnostics below.
output_reader_gone=0
say() {
  printf '%s\n' "$*" 2>/dev/null || output_reader_gone=1
}

if [[ "$#" -lt 1 || "$#" -gt 2 || ("$1" != "quick" && "$1" != "full") ]]; then
  echo "Usage: $0 <quick|full> [lanes]" >&2 || true
  exit 2
fi

gate="$1"
# Lane selector for the quick gate (see scripts/check-quick.sh). The full gate
# always runs every lane.
lanes="${2:-backend frontend rust}"
if [[ "$lanes" == "static" ]]; then
  :
else
  for lane in $lanes; do
    case "$lane" in
      backend|frontend|rust) ;;
      *)
        echo "Unsupported lanes: $lanes" >&2 || true
        exit 2
        ;;
    esac
  done
fi
if [[ "$gate" == "full" && "$lanes" != "backend frontend rust" ]]; then
  echo "Lane selection is only supported for the quick gate." >&2 || true
  exit 2
fi
# Test tier for the quick gate's backend lane: smoke/unit (all pure tests with
# PostgreSQL offline) or full (the unit layer since PR #225 — the postgres
# integration layer runs on CI or via an explicit GATE_TIER=postgres run, and
# scripts/check.sh still covers both tiers for the local full gate); aff is
# the agent inner-loop
# combination (unit backend + vitest related frontend) and never produces
# reusable push evidence — run-local-gate requires the quick/full gates, so aff
# is only reachable through scripts/check-quick.sh directly. The full gate
# always runs the full tier. The tier is part of the evidence fingerprint below.
tier="${GATE_TIER:-full}"
case "$tier" in
  smoke|unit|postgres|full) ;;
  aff)
    echo "GATE_TIER=aff is an inner-loop tier; it produces no push evidence." >&2 || true
    echo "Run scripts/check-quick.sh directly for the inner loop, or use smoke/quick here." >&2 || true
    exit 2
    ;;
  *)
    echo "Unsupported GATE_TIER: $tier" >&2 || true
    exit 2
    ;;
esac
if [[ "$gate" == "full" && "$tier" != "full" ]]; then
  echo "The full gate only supports the full tier." >&2 || true
  exit 2
fi
export GATE_TIER="$tier"
ROOT_DIR="$(git rev-parse --show-toplevel)"
cd "$ROOT_DIR"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Local $gate gate refused: the worktree is not clean." >&2 || true
  echo "Commit or stash all changes so the verified SHA matches the pushed SHA." >&2 || true
  exit 1
fi

head_sha="$(git rev-parse HEAD)"
common_dir="$(git rev-parse --git-common-dir)"
if [[ "$common_dir" != /* ]]; then
  common_dir="$ROOT_DIR/$common_dir"
fi

fingerprint_input="gate=$gate"$'\n'"lanes=$lanes"$'\n'"tier=$tier"$'\n'
fingerprint_paths=(
  scripts/check-fast.sh
  scripts/check-quick.sh
  scripts/check.sh
  scripts/check-ci.sh
  scripts/run-local-gate.sh
  pyproject.toml
  uv.lock
  frontend/package.json
  frontend/package-lock.json
  frontend/vite.config.ts
  config/architecture/architecture-invariants.yaml
  config/architecture/architecture-exemptions.yaml
  velites/Cargo.toml
  velites/Cargo.lock
)

for path in "${fingerprint_paths[@]}"; do
  if [[ -f "$path" ]]; then
    fingerprint_input+="$path=$(git hash-object "$path")"$'\n'
  fi
done

for command_name in uv python3 node npm cargo rustc; do
  if command -v "$command_name" >/dev/null 2>&1; then
    version="$($command_name --version 2>&1 || true)"
    fingerprint_input+="$command_name=${version%%$'\n'*}"$'\n'
  fi
done

# Machine identity is part of the fingerprint (#206): the evidence cache is
# shared across worktrees via the common git dir, so without it machine A's
# pass would be replayed on machine B for the same SHA + toolchain. uname -srm
# alone is NOT a host identity (identical OS/arch machines share it — codex
# review), so resolve a per-host unique id: AGENT_LEGION_MACHINE_ID_FILE
# override (also the test seam), /etc/machine-id (or its dbus mirror) on
# Linux, IOPlatformUUID on macOS, hostname as the last resort.
resolve_machine_identity() {
  local candidate override
  override="${AGENT_LEGION_MACHINE_ID_FILE:-}"
  if [[ -n "$override" && -r "$override" ]]; then
    candidate="$(tr -d '[:space:]' <"$override")"
    if [[ -n "$candidate" ]]; then
      printf 'machine-id:%s' "$candidate"
      return 0
    fi
  fi
  for candidate in /etc/machine-id /var/lib/dbus/machine-id; do
    if [[ -r "$candidate" ]]; then
      candidate="$(tr -d '[:space:]' <"$candidate")"
      if [[ -n "$candidate" ]]; then
        printf 'machine-id:%s' "$candidate"
        return 0
      fi
    fi
  done
  if [[ "$(uname -s)" == "Darwin" ]]; then
    candidate="$(ioreg -rd1 -c IOPlatformExpertDevice 2>/dev/null | sed -n 's/.*IOPlatformUUID" = "\([^"]*\)".*/\1/p' | head -n 1)"
    if [[ -n "$candidate" ]]; then
      printf 'platform-uuid:%s' "$candidate"
      return 0
    fi
  fi
  candidate="$(hostname 2>/dev/null || true)"
  printf 'hostname:%s' "${candidate:-unknown}"
}
machine_identity="$(resolve_machine_identity)"
fingerprint_input+="machine=${machine_identity%%$'\n'*}"$'\n'

fingerprint="$(printf '%s' "$fingerprint_input" | git hash-object --stdin)"
cache_dir="$common_dir/local-gates/$head_sha"
cache_file="$cache_dir/$gate-$fingerprint.pass"

if [[ "${AGENT_LEGION_LOCAL_GATE_FORCE:-0}" != "1" && -f "$cache_file" ]]; then
  # One guarded line on cached-evidence replay (the "push twice" second
  # attempt): it must never be able to fail the push (#679).
  say "Local $gate gate already passed for ${head_sha:0:12}; reusing cached evidence."
  exit 0
fi

case "$gate" in
  quick) gate_script="$ROOT_DIR/scripts/check-quick.sh" ;;
  full) gate_script="$ROOT_DIR/scripts/check.sh" ;;
esac

# Push-path diagnostics (issue #679). A SIGPIPE anywhere in this hook chain
# can only fail the hook, which git reports as "failed to push" with exit 1 —
# never as exit 141. A `git push` that itself exits 141 after a passing gate
# is git dying on one of ITS OWN pipes (git resets SIGPIPE to the default at
# startup, so no trap here can reach it): the remote transport child it
# spawned before running this hook (remote helper / ssh / receive-pack) died
# while the gate held the push open — git then writes the ref updates into the
# dead pipe and the remote ref never moves — or the push's output reader went
# away. Both windows are as long as the gate (queue wait included), hence the
# load dependence and why the cached-evidence retry always passes.
# .githooks/pre-push exports git's pid; snapshot its transport children now
# and re-check them after the gate.
push_git_pid="${AGENT_LEGION_PRE_PUSH_GIT_PID:-}"
diag_file="$common_dir/local-gates/push-diagnostics.log"
push_diag() {
  {
    mkdir -p "${diag_file%/*}" &&
      printf '%s pid=%s git=%s head=%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "$$" "${push_git_pid:-none}" "${head_sha:0:12}" "$*" >>"$diag_file"
  } 2>/dev/null || true
}
git_children() {
  ps -A -o pid= -o ppid= 2>/dev/null |
    awk -v parent="$1" -v self="$$" '$2 == parent && $1 != self { print $1 }' || true
}
# Gone or a zombie: git cannot reap its dead transport while it blocks on
# this hook, so kill -0 alone would still report it alive.
process_dead() {
  local state
  state="$(ps -o stat= -p "$1" 2>/dev/null || true)"
  [[ -z "$state" || "$state" == Z* ]]
}
transport_pids=""
if [[ -n "$push_git_pid" ]]; then
  transport_pids="$(git_children "$push_git_pid" | tr '\n' ' ')"
fi

started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
gate_started_seconds=$SECONDS
say "Running local $gate gate for ${head_sha:0:12} (lanes: $lanes)..."
# The gate script streams the lanes' output through this process's stdout —
# which under pre-push is git push's own pipe. A reader that walked away
# (issue #679) must not turn the chatter into a failed push: with SIGPIPE
# ignored above and every write in the gate scripts guarded, the gate either
# drains the pipe (reader alive) or drops its chatter (reader gone) and in
# both cases reports its verdict through the exit status alone.
gate_status=0
GATE_LANES="$lanes" "$gate_script" || gate_status=$?
if [[ "$gate_status" -ne 0 ]]; then
  if [[ "$gate_status" -gt 128 ]]; then
    # A signal death (141 = SIGPIPE) inside the gate: name the stage instead
    # of leaving a bare exit code behind.
    push_diag "gate script $gate_script died with status $gate_status (signal $((gate_status - 128)))"
    say "Local $gate gate died with status $gate_status (signal $((gate_status - 128))); see $diag_file" >&2
  fi
  exit "$gate_status"
fi
gate_elapsed=$((SECONDS - gate_started_seconds))

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Local $gate gate changed the worktree; refusing to record passing evidence." >&2 || true
  exit 1
fi

mkdir -p "$cache_dir"
temp_file="$cache_file.tmp.$$"
{
  echo "commit=$head_sha"
  echo "gate=$gate"
  echo "fingerprint=$fingerprint"
  echo "started_at=$started_at"
  echo "finished_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "host=$(uname -srm)"
  printf '%s' "$fingerprint_input"
} >"$temp_file"
mv "$temp_file" "$cache_file"

say "Local $gate gate passed for ${head_sha:0:12}."
say "Evidence: $cache_file"

# Post-gate transport check (issue #679): a dead transport means git would
# die with SIGPIPE (141) the moment this hook returns 0, the ref never moving.
# Fail explicitly instead — git reports a plain "failed to push" — with the
# evidence already cached so the re-run is instant.
dead_transport=""
for pid in $transport_pids; do
  if process_dead "$pid"; then
    dead_transport="${dead_transport:+$dead_transport }$pid"
  fi
done
if [[ -n "$dead_transport" ]]; then
  push_diag "transport pid(s) $dead_transport of git push exited during the $gate gate (${gate_elapsed}s); push refused instead of a SIGPIPE (141) death"
  say "The push connection (git transport pid(s) $dead_transport) closed while the ${gate_elapsed}s gate ran;" >&2
  say "git would die with SIGPIPE (141) without updating the remote. The gate passed and its evidence" >&2
  say "is cached: re-run git push (it replays the evidence in seconds). Diagnostics: $diag_file" >&2
  exit 1
fi
if [[ "$output_reader_gone" -eq 1 && -n "$push_git_pid" ]]; then
  # Nothing here can save git's own status writes; the record attributes the
  # 141 that may follow (the ref update itself still goes through).
  push_diag "push output reader went away during the $gate gate (${gate_elapsed}s); git push may exit 141 printing its status after updating the ref — verify with git ls-remote"
fi
