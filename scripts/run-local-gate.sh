#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 1 || "$#" -gt 2 || ("$1" != "quick" && "$1" != "full") ]]; then
  echo "Usage: $0 <quick|full> [lanes]" >&2
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
        echo "Unsupported lanes: $lanes" >&2
        exit 2
        ;;
    esac
  done
fi
if [[ "$gate" == "full" && "$lanes" != "backend frontend rust" ]]; then
  echo "Lane selection is only supported for the quick gate." >&2
  exit 2
fi
# Test tier for the quick gate's backend lane: smoke/unit (all pure tests with
# PostgreSQL offline) or full (whole quick suite); aff is the agent inner-loop
# combination (unit backend + vitest related frontend) and never produces
# reusable push evidence — run-local-gate requires the quick/full gates, so aff
# is only reachable through scripts/check-quick.sh directly. The full gate
# always runs the full tier. The tier is part of the evidence fingerprint below.
tier="${GATE_TIER:-full}"
case "$tier" in
  smoke|unit|postgres|full) ;;
  aff)
    echo "GATE_TIER=aff is an inner-loop tier; it produces no push evidence." >&2
    echo "Run scripts/check-quick.sh directly for the inner loop, or use smoke/quick here." >&2
    exit 2
    ;;
  *)
    echo "Unsupported GATE_TIER: $tier" >&2
    exit 2
    ;;
esac
if [[ "$gate" == "full" && "$tier" != "full" ]]; then
  echo "The full gate only supports the full tier." >&2
  exit 2
fi
export GATE_TIER="$tier"
ROOT_DIR="$(git rev-parse --show-toplevel)"
cd "$ROOT_DIR"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Local $gate gate refused: the worktree is not clean." >&2
  echo "Commit or stash all changes so the verified SHA matches the pushed SHA." >&2
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
# pass would be replayed on machine B for the same SHA + toolchain.
machine_id="$(uname -srm)"
fingerprint_input+="machine=${machine_id%%$'\n'*}"$'\n'

fingerprint="$(printf '%s' "$fingerprint_input" | git hash-object --stdin)"
cache_dir="$common_dir/local-gates/$head_sha"
cache_file="$cache_dir/$gate-$fingerprint.pass"

if [[ "${AGENT_LEGION_LOCAL_GATE_FORCE:-0}" != "1" && -f "$cache_file" ]]; then
  echo "Local $gate gate already passed for ${head_sha:0:12}; reusing cached evidence."
  exit 0
fi

case "$gate" in
  quick) gate_script="$ROOT_DIR/scripts/check-quick.sh" ;;
  full) gate_script="$ROOT_DIR/scripts/check.sh" ;;
esac

started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "Running local $gate gate for ${head_sha:0:12} (lanes: $lanes)..."
GATE_LANES="$lanes" "$gate_script"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Local $gate gate changed the worktree; refusing to record passing evidence." >&2
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

echo "Local $gate gate passed for ${head_sha:0:12}."
echo "Evidence: $cache_file"
