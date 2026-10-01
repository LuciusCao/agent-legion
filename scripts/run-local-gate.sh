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
say() {
  printf '%s\n' "$*" || true
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

started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
say "Running local $gate gate for ${head_sha:0:12} (lanes: $lanes)..."
# The gate script streams the lanes' output through this process's stdout —
# which under pre-push is git push's own pipe. A reader that walked away
# (issue #679) must not turn the chatter into a failed push: with SIGPIPE
# ignored above and every write in the gate scripts guarded, the gate either
# drains the pipe (reader alive) or drops its chatter (reader gone) and in
# both cases reports its verdict through the exit status alone.
GATE_LANES="$lanes" "$gate_script"

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
